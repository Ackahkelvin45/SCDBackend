"""Input validation (DRF serializers) and response shapes (plain dicts matching the app's lib/api/types.ts)."""
from decimal import ROUND_HALF_UP, Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import serializers

from config.errors import ApiError
from .models import HUBaseline, Patient, PredictionRun, Visit
from . import rules
from .rules import LABS, num

User = get_user_model()


# ─── Small shared shapes ─────────────────────────────────────────────────────

def user_ref(user):
    return {'id': str(user.pk), 'full_name': user.full_name} if user else None


def flag_summary(flag):
    return {
        'id': str(flag.pk), 'visit_id': str(flag.visit_id), 'rule': flag.rule, 'severity': flag.severity,
        'message': flag.message, 'status': flag.status, 'acknowledged': flag.acknowledged_at is not None,
        'action': flag.action,
    }


def run_data(run):
    """PredictionRun shape (multi-model-prediction.md v2 §4.2–4.3): every registered model, in /config/ order."""
    stored = {r.model: r for r in run.results.all()}
    outcome_known = run.outcome in ('confirmed', 'not_confirmed')
    results, votes = [], []
    for spec in settings.PREDICTION_MODELS:
        r = stored.get(spec['id'])
        if r is None:
            results.append({'model': spec['id'], 'model_name': spec['name'],
                            'error': {'code': 'model_unavailable', 'detail': f"{spec['name']} unavailable, try again later."}})
            continue
        votes.append(r.predicted_class == 'responder')
        results.append({
            'id': str(r.pk), 'model': r.model, 'model_name': spec['name'],
            'response_probability': r.response_probability, 'predicted_class': r.predicted_class,
            'threshold': r.threshold, 'model_hash': r.model_hash,
            'correct': ((r.predicted_class == 'responder') == (run.outcome == 'confirmed')) if outcome_known else None,
        })
    visit = run.visit
    delta = round(float(visit.hgb) - float(run.baseline_hgb), 2) if visit.hgb is not None else None
    return {
        'run_id': str(run.pk),
        'visit_id': str(run.visit_id),
        'status': run.status,
        'baseline_version_id': run.baseline_version_id,
        'imputed_lab_count': run.imputed_lab_count,
        'reliability_note': run.reliability_note,
        'observed_response': {'delta_hgb': delta,
                              'meets_criterion': None if delta is None else delta >= settings.RESPONSE_THRESHOLD_GDL},
        'results': results,
        'agreement': {'responder_votes': sum(votes), 'of': len(votes), 'all_agree': len(set(votes)) <= 1},
        'observed_outcome': None if run.outcome is None else {
            'next_visit_id': str(run.outcome_visit_id) if run.outcome_visit_id else None,
            'delta_hgb': num(run.outcome_delta_hgb),
            'meets_criterion': run.outcome == 'confirmed' if outcome_known else None,
            'assessable': run.outcome != 'not_assessable',
        },
        'created_at': run.created_at,
        'created_by': user_ref(run.created_by),
    }


def baseline_data(baseline):
    return {
        'baseline_hgb': num(baseline.baseline_hgb), 'baseline_source': baseline.baseline_source,
        'hu_start_date': baseline.hu_start_date, 'version_id': baseline.version_id,
    }


# ─── Patients ────────────────────────────────────────────────────────────────

PATIENT_FIELDS = [
    'folder_number', 'full_name', 'date_of_birth', 'sex', 'genotype', 'phone_number', 'next_of_kin_name',
    'next_of_kin_phone', 'data_consent', 'data_consent_date', 'research_consent', 'research_consent_date',
    'anc_flag_floor_reason', 'archived',
]


class PatientSerializer(serializers.ModelSerializer):
    primary_clinician_id = serializers.PrimaryKeyRelatedField(
        source='primary_clinician', queryset=User.objects.none(), required=False, allow_null=True)
    anc_flag_floor = serializers.FloatField(required=False)
    # The app sends null when the floor is the default 2.0
    anc_flag_floor_reason = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=500)
    confirm_duplicate = serializers.BooleanField(required=False, write_only=True)

    class Meta:
        model = Patient
        fields = PATIENT_FIELDS + ['primary_clinician_id', 'anc_flag_floor', 'confirm_duplicate']
        extra_kwargs = {'data_consent': {'required': True}}  # partial updates may still omit it

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['primary_clinician_id'].queryset = User.objects.filter(
            facility=self.context['facility'], is_active=True)

    def validate_data_consent(self, value):
        if not value:
            raise serializers.ValidationError('Data-processing consent is required to register a patient.')
        return value

    def validate_anc_flag_floor_reason(self, value):
        return value or ''

    def validate_date_of_birth(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('Date of birth cannot be in the future.')
        return value

    def validate_anc_flag_floor(self, value):
        if value not in (2.0, 1.25):
            raise serializers.ValidationError('ANC flag floor must be 2.0 or 1.25.')
        return Decimal(str(value))

    def validate(self, attrs):
        floor = attrs.get('anc_flag_floor', getattr(self.instance, 'anc_flag_floor', Decimal('2.00')))
        reason = attrs.get('anc_flag_floor_reason', getattr(self.instance, 'anc_flag_floor_reason', ''))
        if floor == Decimal('1.25') and not (reason or '').strip():
            raise serializers.ValidationError({'anc_flag_floor_reason': 'Give a reason for lowering the ANC flag floor.'})
        return attrs


def patient_data(patient, period='all'):
    """Patient detail plus the summary block (requirements §2)."""
    today = timezone.localdate()
    baseline = rules.baseline_of(patient)
    visits = rules.chronological(patient)
    latest = visits[-1] if visits else None
    last_run = patient_runs(patient).first()
    data = {f: getattr(patient, f) for f in PATIENT_FIELDS}
    data.update({
        'id': str(patient.pk),
        'anc_flag_floor': num(patient.anc_flag_floor),
        'primary_clinician_id': str(patient.primary_clinician_id) if patient.primary_clinician_id else None,
        'primary_clinician': user_ref(patient.primary_clinician),
        'age_years': rules.age_at(patient.date_of_birth, today),
        'hu': None if baseline is None else {
            'baseline_hgb': num(baseline.baseline_hgb), 'baseline_source': baseline.baseline_source,
            'hu_start_date': baseline.hu_start_date, 'days_on_hu': (today - baseline.hu_start_date).days,
            'baseline_version_id': baseline.version_id,
        },
        'latest_visit': None if latest is None else {
            'id': str(latest.pk), 'lab_sample_date': latest.lab_sample_date, 'hgb': num(latest.hgb)},
        'next_appointment_date': latest.next_appointment_date if latest else None,
        'overdue': bool(latest and latest.next_appointment_date and latest.next_appointment_date < today),
        'observed_response': rules.observed_response(latest, baseline),
        'active_flags': [flag_summary(f) for f in patient.flags.filter(acknowledged_at__isnull=True, status='active')
                         .select_related('visit')],
        'latest_prediction': run_data(last_run) if last_run else None,
        'prediction_eligibility': rules.eligibility(latest) if latest else None,
        'analytics': rules.analytics(patient, baseline, visits, period),
    })
    return data


def patient_runs(patient):
    return (PredictionRun.objects.filter(visit__patient=patient)
            .select_related('visit', 'created_by').prefetch_related('results'))


# ─── HU baseline ─────────────────────────────────────────────────────────────

class BaselineSerializer(serializers.ModelSerializer):
    baseline_hgb = serializers.DecimalField(max_digits=4, decimal_places=1, rounding=ROUND_HALF_UP,
                                            min_value=Decimal('3'), max_value=Decimal('15'))
    reason = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = HUBaseline
        fields = ['baseline_hgb', 'baseline_source', 'hu_start_date', 'reason']

    def validate_hu_start_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('HU start date cannot be in the future.')
        return value

    def validate(self, attrs):
        if self.instance is not None and not (attrs.get('reason') or '').strip():
            raise serializers.ValidationError({'reason': 'Give a reason for changing the baseline.'})
        return attrs


# ─── Visits ──────────────────────────────────────────────────────────────────

VISIT_FIELDS = [
    'lab_sample_date', 'consult_date', *LABS, 'weight_kg', 'dose_mg', 'adherence', 'transfused_last_3_months',
    'pregnancy_status', 'contraception_counselled', 'toxicity_nausea', 'toxicity_vomiting', 'toxicity_rash',
    'toxicity_fatigue', 'toxicity_headache', 'pain_episodes', 'next_appointment_date', 'note',
]


class VisitSerializer(serializers.ModelSerializer):
    confirm_out_of_range = serializers.BooleanField(required=False, write_only=True)
    confirm_duplicate = serializers.BooleanField(required=False, write_only=True)
    edit_reason = serializers.CharField(required=False, allow_blank=True, write_only=True)

    class Meta:
        model = Visit
        fields = VISIT_FIELDS + ['confirm_out_of_range', 'confirm_duplicate', 'edit_reason']
        # Round extra decimals instead of rejecting them (the app may send 8.649999…)
        extra_kwargs = {f: {'rounding': ROUND_HALF_UP} for f in LABS + ['weight_kg']}

    def validate_lab_sample_date(self, value):
        if value > timezone.localdate():
            raise serializers.ValidationError('The lab sample date cannot be in the future.')
        return value

    def validate_weight_kg(self, value):
        if value is not None and value <= 0:
            raise serializers.ValidationError('Weight must be more than 0 kg.')
        return value

    def validate(self, attrs):
        patient = self.context['patient']
        merged = {f: attrs.get(f, getattr(self.instance, f, None)) for f in VISIT_FIELDS}

        if self.instance is not None and not (attrs.get('edit_reason') or '').strip():
            raise serializers.ValidationError({'edit_reason': 'Give a reason for editing this visit.'})

        lab_date = merged['lab_sample_date']
        if (patient.sex == 'female' and 12 <= rules.age_at(patient.date_of_birth, lab_date) <= 50
                and not merged['pregnancy_status']):
            raise serializers.ValidationError({'pregnancy_status': 'Required for female patients aged 12–50.'})

        if not attrs.get('confirm_out_of_range'):
            out = {}
            for lab, r in settings.LAB_RANGES.items():
                value = attrs.get(lab)
                if value is not None and not r['min'] <= float(value) <= r['max']:
                    out[lab] = [f"Outside the plausible range {r['min']:g}–{r['max']:g} {r['unit']}. Check the value and confirm."]
            if out:
                raise ApiError(400, 'out_of_range', 'Some values are outside the plausible range.', fields=out)

        if 'lab_sample_date' in attrs and not attrs.get('confirm_duplicate'):
            same_day = patient.visits.filter(lab_sample_date=lab_date)
            if self.instance is not None:
                same_day = same_day.exclude(pk=self.instance.pk)
            if same_day.exists():
                raise ApiError(409, 'duplicate_visit_date', 'A visit already exists on this lab sample date.')
        return attrs


def latest_run(visit):
    return visit.prediction_runs.select_related('visit', 'created_by').prefetch_related('results').first()


def visit_data(visit):
    patient = visit.patient
    visits = rules.chronological(patient)
    prev = rules.previous_visit(visit, visits)
    run = latest_run(visit)
    data = {f: getattr(visit, f) for f in VISIT_FIELDS}
    for f in LABS + ['weight_kg']:
        data[f] = num(data[f])
    data.update({
        'id': str(visit.pk),
        'patient_id': str(patient.pk),
        'dose_mg_per_kg': visit.dose_mg_per_kg,
        'flags': [flag_summary(f) for f in visit.flags.all()],
        'deltas': {lab: (round(float(getattr(visit, lab)) - float(getattr(prev, lab)), 2)
                         if prev and getattr(visit, lab) is not None and getattr(prev, lab) is not None else None)
                   for lab in LABS},
        'observed_response': rules.observed_response(visit, rules.baseline_of(patient)),
        'prediction': run_data(run) if run else None,
        'edited': visit.edited,
        'created_by': user_ref(visit.created_by),
        'created_at': visit.created_at,
    })
    return data


def history_row(visit, prev_visit, prev_dosed, baseline, default_model):
    """One row of GET /patients/{id}/visits/ (requirements §4)."""
    run = next(iter(visit.prediction_runs.all()), None)  # prefetched newest first by PatientVisitsView
    prediction = None
    if run is not None:
        result = next((r for r in run.results.all() if r.model == default_model), None) or run.results.first()
        if result is not None:
            prediction = {'predicted_class': result.predicted_class, 'response_probability': result.response_probability,
                          'status': run.status, 'outcome': run.outcome}
    hgb, prev_hgb = num(visit.hgb), num(prev_visit.hgb) if prev_visit else None
    return {
        'id': str(visit.pk),
        'lab_sample_date': visit.lab_sample_date,
        'hgb': hgb, 'anc': num(visit.anc), 'platelet_count': num(visit.platelet_count),
        'delta_hgb_vs_previous': round(hgb - prev_hgb, 2) if hgb is not None and prev_hgb is not None else None,
        'delta_hgb_vs_baseline': round(hgb - float(baseline.baseline_hgb), 2) if hgb is not None and baseline else None,
        'dose_mg': visit.dose_mg,
        'dose_mg_per_kg': visit.dose_mg_per_kg,
        'dose_changed': bool(visit.dose_mg is not None and prev_dosed and prev_dosed.dose_mg != visit.dose_mg),
        'previous_dose_mg': prev_dosed.dose_mg if prev_dosed else None,
        'adherence': visit.adherence,
        'transfused_last_3_months': visit.transfused_last_3_months,
        'pain_episodes': visit.pain_episodes,
        'flags': [flag_summary(f) for f in visit.flags.all()],
        'prediction': prediction,
        'edited': visit.edited,
        'created_by': user_ref(visit.created_by),
    }
