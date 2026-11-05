import logging

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.db.models import Case, Count, Exists, F, IntegerField, Max, OuterRef, Prefetch, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import permissions, serializers, status
from rest_framework.pagination import LimitOffsetPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import Facility
from config.errors import ApiError
from ml_app import predictor
from . import rules
from .models import HUBaseline, ModelPrediction, Patient, PredictionRun, SafetyFlag, Visit
from .serializers import (
    BaselineSerializer, PatientSerializer, VisitSerializer, baseline_data, history_row, patient_data,
    patient_runs, run_data, user_ref, visit_data,
)

logger = logging.getLogger(__name__)
User = get_user_model()


def paginate(request, view, items, to_dict):
    paginator = LimitOffsetPagination()
    page = paginator.paginate_queryset(items, request, view=view)
    return paginator.get_paginated_response([to_dict(x) for x in page])


def my_patient(request, pk):
    """Facility scoping: anything outside the clinician's facility is a 404."""
    return get_object_or_404(Patient, pk=pk, facility=request.user.facility)


def my_visit(request, pk):
    return get_object_or_404(Visit.objects.select_related('patient__facility'), pk=pk,
                             patient__facility=request.user.facility)


def folder_taken():
    return ApiError(409, 'folder_number_taken', 'This folder number is already registered at your facility.',
                    fields={'folder_number': ['Already registered at your facility.']})


def save_patient(patient):
    """The exists() pre-check can race; the unique constraint is the real guard."""
    try:
        with transaction.atomic():
            patient.save()
    except IntegrityError as e:
        if 'folder_number' in str(e):
            raise folder_taken()
        raise


def stamp(instance, user, reason=None):
    """Attribute the change in simple-history (DRF authenticates after middleware, so set it explicitly)."""
    instance._history_user = user
    if reason:
        instance._change_reason = reason


# ─── Account-level lookups ──────────────────────────────────────────────────

class FacilityListView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response([{'id': str(f.pk), 'name': f.name, 'region': f.region} for f in Facility.objects.all()])


class ClinicianListView(APIView):
    def get(self, request):
        clinicians = User.objects.filter(facility=request.user.facility, is_active=True).order_by('full_name')
        return Response([user_ref(u) for u in clinicians])


class ConfigView(APIView):
    def get(self, request):
        models = []
        for i, spec in enumerate(settings.PREDICTION_MODELS):
            try:
                threshold, model_hash = round(predictor.threshold(spec['id']), 4), predictor.model_hash(spec['id'])
            except Exception:
                logger.exception('Could not read model artefacts for %s', spec['id'])
                threshold, model_hash = None, None
            models.append({'id': spec['id'], 'name': spec['name'], 'is_default': i == 0, 'threshold': threshold,
                           'test_metrics': spec['test_metrics'], 'model_hash': model_hash})
        return Response({
            'lab_ranges': settings.LAB_RANGES,
            'flag_thresholds': settings.FLAG_THRESHOLDS,
            'prediction_models': models,
        })


# ─── Patients ────────────────────────────────────────────────────────────────

class PatientListView(APIView):
    def get(self, request):
        p = request.query_params
        open_flags = SafetyFlag.objects.filter(patient=OuterRef('pk'), acknowledged_at__isnull=True, status='active')
        qs = (Patient.objects.filter(facility=request.user.facility)
              .annotate(last_visit_date=Max('visits__lab_sample_date'),
                        active_flag_count=Count('flags', filter=Q(flags__acknowledged_at__isnull=True,
                                                                  flags__status='active'), distinct=True),
                        has_baseline=Exists(HUBaseline.objects.filter(patient=OuterRef('pk'))),
                        max_flag_severity=Case(
                            When(Exists(open_flags.filter(severity='critical')), then=Value('critical')),
                            When(Exists(open_flags.filter(severity='warning')), then=Value('warning')),
                            When(Exists(open_flags.filter(severity='info')), then=Value('info')),
                            default=Value(None))))
        if p.get('archived') != 'true':
            qs = qs.filter(archived=False)
        if search := p.get('search', '').strip():
            qs = qs.filter(Q(full_name__icontains=search) | Q(folder_number__icontains=search))
        if p.get('has_flags') == 'true':
            qs = qs.filter(active_flag_count__gt=0)
        if p.get('baseline_missing') == 'true':
            qs = qs.filter(has_baseline=False)
        if p.get('ordering') == '-last_visit_date':
            qs = qs.order_by(F('last_visit_date').desc(nulls_last=True), 'full_name')
        else:
            qs = qs.order_by('full_name')
        return paginate(request, self, qs, lambda x: {
            'id': str(x.pk), 'folder_number': x.folder_number, 'full_name': x.full_name,
            'date_of_birth': x.date_of_birth, 'sex': x.sex, 'genotype': x.genotype,
            'last_visit_date': x.last_visit_date, 'active_flag_count': x.active_flag_count,
            'max_flag_severity': x.max_flag_severity, 'baseline_missing': not x.has_baseline,
        })

    def post(self, request):
        facility = request.user.facility
        if facility is None:
            raise ApiError(403, 'no_facility', 'Your account is not linked to a facility.')
        s = PatientSerializer(data=request.data, context={'facility': facility})
        s.is_valid(raise_exception=True)
        data = s.validated_data
        if Patient.objects.filter(facility=facility, folder_number=data['folder_number']).exists():
            raise folder_taken()
        if not data.pop('confirm_duplicate', False):
            matches = Patient.objects.filter(facility=facility, full_name__iexact=data['full_name'].strip(),
                                             date_of_birth=data['date_of_birth'])
            if matches.exists():
                raise ApiError(409, 'possible_duplicate', 'A patient with the same name and date of birth exists.',
                               matches=[{'id': str(m.pk), 'full_name': m.full_name, 'folder_number': m.folder_number}
                                        for m in matches])
        patient = Patient(facility=facility, created_by=request.user, updated_by=request.user, **data)
        stamp(patient, request.user)
        save_patient(patient)
        return Response(patient_data(patient), status=status.HTTP_201_CREATED)


class PatientDetailView(APIView):
    def get(self, request, pk):
        period = request.query_params.get('period', 'all')
        return Response(patient_data(my_patient(request, pk), period if period in ('6m', '12m') else 'all'))

    def patch(self, request, pk):
        patient = my_patient(request, pk)
        s = PatientSerializer(patient, data=request.data, partial=True, context={'facility': patient.facility})
        s.is_valid(raise_exception=True)
        data = s.validated_data
        data.pop('confirm_duplicate', None)
        folder = data.get('folder_number')
        if folder and Patient.objects.filter(facility=patient.facility, folder_number=folder).exclude(pk=patient.pk).exists():
            raise folder_taken()
        model_inputs_changed = any(k in data and data[k] != getattr(patient, k) for k in ('date_of_birth', 'sex', 'genotype'))
        with transaction.atomic():
            for k, v in data.items():
                setattr(patient, k, v)
            patient.updated_by = request.user
            stamp(patient, request.user)
            save_patient(patient)
            if model_inputs_changed:
                rules.outdate_runs(PredictionRun.objects.filter(visit__patient=patient))
        return Response(patient_data(patient))


# ─── HU baseline ─────────────────────────────────────────────────────────────

class BaselineView(APIView):
    def get(self, request, pk):
        baseline = get_object_or_404(HUBaseline, patient=my_patient(request, pk))
        return Response(baseline_data(baseline))

    def put(self, request, pk):
        patient = my_patient(request, pk)
        baseline = rules.baseline_of(patient)
        s = BaselineSerializer(baseline, data=request.data)
        s.is_valid(raise_exception=True)
        data = s.validated_data
        reason = data.pop('reason', '')
        with transaction.atomic():
            if baseline is None:
                baseline = HUBaseline(patient=patient)
            for k, v in data.items():
                setattr(baseline, k, v)
            baseline.updated_by = request.user
            stamp(baseline, request.user, reason)
            existed = baseline.pk is not None
            baseline.save()
            if existed:
                rules.outdate_runs(PredictionRun.objects.filter(visit__patient=patient))
        return Response(baseline_data(baseline))


class BaselineHistoryView(APIView):
    def get(self, request, pk):
        baseline = get_object_or_404(HUBaseline, patient=my_patient(request, pk))
        return Response([{
            'version_id': h.history_id, 'baseline_hgb': float(h.baseline_hgb), 'baseline_source': h.baseline_source,
            'hu_start_date': h.hu_start_date, 'changed_by': user_ref(h.history_user), 'changed_at': h.history_date,
            'reason': h.history_change_reason,
        } for h in baseline.history.select_related('history_user')])


# ─── Visits ──────────────────────────────────────────────────────────────────

def save_visit(visit, user, reason=None):
    """Save, then recompute everything derived from the visit sequence: flags and prediction outcomes.
    Flags are recomputed from the visit's old or new position, whichever is earlier, since every later visit
    may now have a different previous visit (hgb_drop)."""
    old_date = Visit.objects.filter(pk=visit.pk).values_list('lab_sample_date', flat=True).first()
    visit.updated_by = user
    stamp(visit, user, reason)
    visit.save()
    start = min(d for d in (old_date, visit.lab_sample_date) if d is not None)
    visits = rules.chronological(visit.patient)
    for i, v in enumerate(visits):
        if v.lab_sample_date >= start:
            rules.apply_flags(v, visits[i - 1] if i else None)
    rules.refresh_outcomes(visit.patient)


class PatientVisitsView(APIView):
    def get(self, request, pk):
        patient = my_patient(request, pk)
        baseline = rules.baseline_of(patient)
        runs = PredictionRun.objects.order_by('-created_at').select_related('created_by').prefetch_related('results')
        visits = list(patient.visits.order_by('lab_sample_date', 'created_at')
                      .select_related('created_by').prefetch_related('flags', Prefetch('prediction_runs', runs)))
        default_model = settings.PREDICTION_MODELS[0]['id']
        rows, last_dosed = [], None
        for i, v in enumerate(visits):
            rows.append(history_row(v, visits[i - 1] if i else None, last_dosed, baseline, default_model))
            if v.dose_mg is not None:
                last_dosed = v
        rows.reverse()  # newest first
        return paginate(request, self, rows, lambda r: r)

    def post(self, request, pk):
        patient = my_patient(request, pk)
        s = VisitSerializer(data=request.data, context={'patient': patient})
        s.is_valid(raise_exception=True)
        data = s.validated_data
        for k in ('confirm_out_of_range', 'confirm_duplicate', 'edit_reason'):
            data.pop(k, None)
        with transaction.atomic():
            visit = Visit(patient=patient, created_by=request.user, **data)
            save_visit(visit, request.user)
        return Response(visit_data(visit), status=status.HTTP_201_CREATED)


class VisitDetailView(APIView):
    def get(self, request, pk):
        return Response(visit_data(my_visit(request, pk)))

    def patch(self, request, pk):
        visit = my_visit(request, pk)
        s = VisitSerializer(visit, data=request.data, partial=True, context={'patient': visit.patient})
        s.is_valid(raise_exception=True)
        data = s.validated_data
        reason = data.pop('edit_reason')
        for k in ('confirm_out_of_range', 'confirm_duplicate'):
            data.pop(k, None)
        with transaction.atomic():
            for k, v in data.items():
                setattr(visit, k, v)
            visit.edited = True
            save_visit(visit, request.user, reason)
            rules.outdate_runs(visit.prediction_runs.all())
        return Response(visit_data(visit))


class TrendsView(APIView):
    SERIES = ['hgb', 'anc', 'platelet_count', 'wbc', 'mcv', 'arc', 'hct', 'rbc', 'weight_kg', 'dose_mg_per_kg', 'pain_episodes']

    def get(self, request, pk):
        patient = my_patient(request, pk)
        p = request.query_params
        labs = [l for l in p.get('labs', 'hgb').split(',') if l in self.SERIES] or ['hgb']
        try:
            start = serializers.DateField().to_internal_value(p['from']) if p.get('from') else None
            end = serializers.DateField().to_internal_value(p['to']) if p.get('to') else None
        except serializers.ValidationError:
            raise ApiError(400, 'invalid', 'from and to must be dates (YYYY-MM-DD).')
        visits = [v for v in rules.chronological(patient)
                  if (start is None or v.lab_sample_date >= start) and (end is None or v.lab_sample_date <= end)]

        series = {}
        for lab in labs:
            series[lab] = [{'date': v.lab_sample_date, 'value': rules.num(getattr(v, lab)), 'visit_id': str(v.pk)}
                           for v in visits if getattr(v, lab) is not None]

        baseline = rules.baseline_of(patient)
        t = settings.FLAG_THRESHOLDS
        reference_lines = {
            'anc': [{'label': 'Flag floor', 'value': float(patient.anc_flag_floor)}],
            'platelet_count': [{'label': 'Flag', 'value': t['platelets_low']}],
            'arc': [{'label': 'Flag', 'value': t['arc_low']}],
        }
        if baseline:
            reference_lines['hgb'] = [
                {'label': 'Baseline', 'value': float(baseline.baseline_hgb)},
                {'label': f"Target (+{settings.RESPONSE_THRESHOLD_GDL:g})",
                 'value': float(baseline.baseline_hgb) + settings.RESPONSE_THRESHOLD_GDL},
            ]
        reference_lines = {k: v for k, v in reference_lines.items() if k in labs}

        def in_range(d):
            return (start is None or d >= start) and (end is None or d <= end)

        events, last_dose = [], None
        for v in rules.chronological(patient):
            if v.dose_mg is not None:
                if last_dose is not None and v.dose_mg != last_dose and in_range(v.lab_sample_date):
                    events.append({'date': v.lab_sample_date, 'type': 'dose_change', 'label': f'{last_dose} → {v.dose_mg} mg'})
                last_dose = v.dose_mg
            if v.transfused_last_3_months == 'yes' and in_range(v.lab_sample_date):
                events.append({'date': v.lab_sample_date, 'type': 'transfusion', 'label': 'Transfused in last 3 months'})
        if baseline:
            versions = list(baseline.history.order_by('history_date'))
            for prev, h in zip(versions, versions[1:]):
                d = timezone.localdate(h.history_date)
                if in_range(d):
                    events.append({'date': d, 'type': 'baseline_change',
                                   'label': f'Baseline {float(prev.baseline_hgb):g} → {float(h.baseline_hgb):g} g/dL'})
        flags = SafetyFlag.objects.filter(patient=patient, status='active', severity__in=('critical', 'warning'))
        for f in flags.select_related('visit'):
            if in_range(f.visit.lab_sample_date):
                events.append({'date': f.visit.lab_sample_date, 'type': 'flag', 'label': f.message})
        events.sort(key=lambda e: e['date'])
        return Response({'series': series, 'reference_lines': reference_lines, 'events': events})


# ─── Safety flags ────────────────────────────────────────────────────────────

class FlagListView(APIView):
    def get(self, request):
        qs = (SafetyFlag.objects.filter(patient__facility=request.user.facility, patient__archived=False, status='active')
              .select_related('patient')
              .annotate(rank=Case(When(severity='critical', then=0), When(severity='warning', then=1), default=2,
                                  output_field=IntegerField()))
              .order_by('rank', '-created_at'))
        acknowledged = request.query_params.get('acknowledged')
        if acknowledged == 'false':
            qs = qs.filter(acknowledged_at__isnull=True)
        elif acknowledged == 'true':
            qs = qs.filter(acknowledged_at__isnull=False)
        return paginate(request, self, qs, lambda f: {
            'id': str(f.pk), 'rule': f.rule, 'severity': f.severity, 'message': f.message,
            'patient': {'id': str(f.patient_id), 'full_name': f.patient.full_name, 'folder_number': f.patient.folder_number},
            'visit_id': str(f.visit_id), 'created_at': f.created_at,
        })


class AcknowledgeSerializer(serializers.Serializer):
    action = serializers.ChoiceField(choices=[a for a, _ in SafetyFlag.ACTIONS])
    note = serializers.CharField(required=False, allow_blank=True)


class FlagAcknowledgeView(APIView):
    def post(self, request, pk):
        flag = get_object_or_404(SafetyFlag, pk=pk, patient__facility=request.user.facility)
        s = AcknowledgeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        if flag.acknowledged_at is not None:
            raise ApiError(409, 'already_acknowledged', 'This flag has already been acknowledged.')
        flag.acknowledged_at = timezone.now()
        flag.acknowledged_by = request.user
        flag.action = s.validated_data['action']
        flag.note = s.validated_data.get('note', '')
        stamp(flag, request.user)
        flag.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ─── Prediction ──────────────────────────────────────────────────────────────

class EligibilityView(APIView):
    def get(self, request, pk):
        return Response(rules.eligibility(my_visit(request, pk)))


def requested_models(request):
    """?model=<id> limits a request to one model (one APK per model in the pilot); absent means all of them."""
    wanted = request.query_params.get('model')
    if not wanted:
        return settings.PREDICTION_MODELS
    specs = [s for s in settings.PREDICTION_MODELS if s['id'] == wanted]
    if not specs:
        raise ApiError(400, 'invalid', 'Unknown model.', fields={'model': [f'Choose one of: {", ".join(s["id"] for s in settings.PREDICTION_MODELS)}.']})
    return specs


class PredictView(APIView):
    def post(self, request, pk):
        visit = my_visit(request, pk)
        patient = visit.patient
        if not patient.facility.prediction_enabled:
            raise ApiError(403, 'prediction_disabled', 'Prediction is not enabled for this facility.')
        specs = requested_models(request)
        data = request.data if isinstance(request.data, dict) else {}
        override_reason = str(data.get('override_reason') or '').strip()
        check = rules.eligibility(visit, override_reason)
        if not check['eligible']:
            raise ApiError(409, 'not_eligible', 'This visit is not eligible for prediction.', reasons=check['reasons'])

        baseline = rules.baseline_of(patient)
        # Lock the visit so a double-tap waits for the first request's run instead of creating a second one
        with transaction.atomic():
            Visit.objects.select_for_update().get(pk=visit.pk)
            run = visit.prediction_runs.filter(status='current').first()
            created = run is None
            if created:
                run = PredictionRun.objects.create(
                    visit=visit, baseline_version_id=baseline.version_id, baseline_hgb=baseline.baseline_hgb,
                    imputed_lab_count=check['imputed_lab_count'], reliability_note=check['reliability_note'],
                    override_reason=override_reason, created_by=request.user)

            # Every requested model runs; a retry fills in only the models missing from the current run
            done = set(run.results.values_list('model', flat=True))
            features = predictor.build_feature_vector(visit, baseline)
            for spec in specs:
                if spec['id'] in done:
                    continue
                try:
                    ModelPrediction.objects.create(run=run, model=spec['id'], **predictor.predict_one(spec['id'], features))
                except Exception:
                    logger.exception('Model %s failed for visit %s', spec['id'], visit.pk)

            if not run.results.filter(model__in=[s['id'] for s in specs]).exists():
                if created:
                    run.delete()
                raise ApiError(503, 'prediction_unavailable', 'Prediction unavailable, try later.')
        rules.refresh_outcomes(patient)
        run.refresh_from_db()
        return Response(run_data(run, specs), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PatientPredictionsView(APIView):
    def get(self, request, pk):
        specs = requested_models(request)
        return paginate(request, self, patient_runs(my_patient(request, pk)), lambda run: run_data(run, specs))
