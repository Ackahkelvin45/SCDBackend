"""Clinical rules the server owns (spec §6–§7): safety flags, prediction eligibility, observed response,
outcome check and the patient analytics. Thresholds come from settings so they can change without code."""
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from .models import HUBaseline, PredictionRun, SafetyFlag

LABS = ['hgb', 'wbc', 'anc', 'platelet_count', 'rbc', 'mcv', 'hct', 'arc']
CORE_LABS = ['anc', 'platelet_count', 'wbc']
LOWER_RELIABILITY_GENOTYPES = {'HbSC', 'HbSβ0', 'HbSβ+'}


def num(value):
    return None if value is None else float(value)


def age_at(date_of_birth, on):
    """Whole years, as a clinician would say it."""
    years = on.year - date_of_birth.year
    return years - ((on.month, on.day) < (date_of_birth.month, date_of_birth.day))


def chronological(patient):
    return list(patient.visits.order_by('lab_sample_date', 'created_at'))


def previous_visit(visit, visits=None):
    visits = visits if visits is not None else chronological(visit.patient)
    i = next(i for i, v in enumerate(visits) if v.pk == visit.pk)
    return visits[i - 1] if i > 0 else None


def baseline_of(patient):
    return HUBaseline.objects.filter(patient=patient).first()


# ─── Safety flags ────────────────────────────────────────────────────────────

def evaluate_flags(visit, previous):
    t = settings.FLAG_THRESHOLDS
    hgb, anc, plt, arc = num(visit.hgb), num(visit.anc), num(visit.platelet_count), num(visit.arc)
    flags = []

    def add(rule, severity, message):
        flags.append({'rule': rule, 'severity': severity, 'message': message})

    if anc is not None and anc < t['anc_critical']:
        add('anc_critical', 'critical', f'ANC {anc:g}: severe neutropenia, hold HU per protocol')
    elif anc is not None and anc < float(visit.patient.anc_flag_floor):
        add('anc_low', 'warning', f'ANC {anc:g}: neutropenia, HU toxicity criterion')
    if hgb is not None and hgb < t['hgb_critical']:
        add('hgb_critical', 'critical', f'Hb {hgb:g}: severe anaemia')
    if visit.pregnancy_status == 'pregnant':
        add('pregnant', 'critical', 'Pregnant: HU is teratogenic, review per local guidance')
    if plt is not None and plt < t['platelets_low']:
        add('platelets_low', 'warning', f'Platelets {plt:g}: thrombocytopenia, HU toxicity criterion')
    if arc is not None and hgb is not None and arc < t['arc_low'] and hgb < t['arc_low_hgb']:
        add('arc_low', 'warning', f'ARC {arc:g} with Hb {hgb:g}: reticulocytopenia, HU toxicity criterion')
    prev_hgb = num(previous.hgb) if previous else None
    if hgb is not None and prev_hgb is not None and prev_hgb - hgb >= t['hgb_drop']:
        add('hgb_drop', 'warning', f'Hb fell {prev_hgb - hgb:.1f} g/dL since the last visit: possible acute anaemia '
                                   '(aplastic crisis or sequestration), not an HU rule')
    if visit.pregnancy_status == 'planning':
        add('pregnancy_planning', 'info', 'Planning pregnancy: discuss HU and conception per local guidance')
    return flags


def apply_flags(visit, previous):
    """Flags are updated in place by rule so their ids stay stable. When a rule stops firing, an unacknowledged
    flag is deleted and an acknowledged one is kept as resolved_by_edit."""
    fresh = {f['rule']: f for f in evaluate_flags(visit, previous)}
    for flag in visit.flags.all():
        f = fresh.pop(flag.rule, None)
        if f is None and flag.acknowledged_at is None:
            flag.delete()
            continue
        new = ('active', f['severity'], f['message']) if f else ('resolved_by_edit', flag.severity, flag.message)
        if (flag.status, flag.severity, flag.message) != new:
            flag.status, flag.severity, flag.message = new
            flag.save(update_fields=['status', 'severity', 'message'])
    for f in fresh.values():
        SafetyFlag.objects.create(visit=visit, patient=visit.patient, **f)


# ─── Observed response and outcome ──────────────────────────────────────────

def observed_response(visit, baseline):
    if visit is None or baseline is None:
        return None
    if visit.hgb is None:
        return {'delta_hgb': None, 'meets_criterion': None}
    delta = round(float(visit.hgb) - float(baseline.baseline_hgb), 2)
    return {'delta_hgb': delta, 'meets_criterion': delta >= settings.RESPONSE_THRESHOLD_GDL}


def refresh_outcomes(patient):
    """Score every prediction run against the visit that followed it, using the baseline version the run used."""
    visits = chronological(patient)
    position = {v.pk: i for i, v in enumerate(visits)}
    for run in PredictionRun.objects.filter(visit__patient=patient):
        i = position[run.visit_id]
        nxt = visits[i + 1] if i + 1 < len(visits) else None
        outcome, delta = None, None
        if nxt is not None:
            if nxt.hgb is not None:
                delta = round(float(nxt.hgb) - float(run.baseline_hgb), 2)
            if nxt.transfused_last_3_months in ('yes', 'unknown'):
                outcome = 'not_assessable'
            elif delta is not None:
                outcome = 'confirmed' if delta >= settings.RESPONSE_THRESHOLD_GDL else 'not_confirmed'
        new = (outcome, nxt.pk if nxt and outcome else None, delta if outcome else None)
        if (run.outcome, run.outcome_visit_id, num(run.outcome_delta_hgb)) != new:
            run.outcome, run.outcome_visit_id, run.outcome_delta_hgb = new
            run.save(update_fields=['outcome', 'outcome_visit', 'outcome_delta_hgb'])


def outdate_runs(runs):
    runs.filter(status='current').update(status='outdated')


# ─── Prediction eligibility (spec §7) ───────────────────────────────────────

def eligibility(visit, override_reason=''):
    patient = visit.patient
    baseline = baseline_of(patient)
    lab_date = visit.lab_sample_date
    age = age_at(patient.date_of_birth, lab_date)
    reasons = []

    def block(code, message, **extra):
        reasons.append({'code': code, 'message': message, **extra})

    if not patient.facility.prediction_enabled:
        block('feature_disabled', 'Prediction is not enabled for this facility.')
    if not patient.research_consent:
        block('no_research_consent', 'No research prediction consent recorded for this patient.')
    if baseline is None:
        block('baseline_missing', 'Set the HU baseline first.')
    elif baseline.baseline_source == 'unknown':
        block('baseline_unknown', 'The baseline Hb source is unknown. Record a measured or folder baseline.')
    else:
        days = (lab_date - baseline.hu_start_date).days
        if days < settings.MIN_DAYS_ON_HU:
            eligible_from = baseline.hu_start_date + timedelta(days=settings.MIN_DAYS_ON_HU)
            block('days_on_hu', f'{days} days on HU at this lab date. Prediction needs {settings.MIN_DAYS_ON_HU}.',
                  eligible_from=eligible_from.isoformat())
    if visit.hgb is None:
        block('hgb_missing', 'Hb is missing for this visit.')
    if any(getattr(visit, lab) is None for lab in CORE_LABS) and not override_reason:
        block('core_labs_missing', 'ANC, platelets or WBC is missing. Enter them, or give a reason to continue without.')
    if visit.transfused_last_3_months in ('yes', 'unknown'):
        block('transfused', 'Transfused in the last 3 months, or not known: Hb is not interpretable.')
    if visit.pregnancy_status in ('pregnant', 'unknown'):
        block('pregnant', 'Pregnant, or pregnancy status not known.')
    if age < 2:
        block('age_out_of_range', 'Under 2 years old at this visit, outside the training population.')
    if patient.genotype in ('other', 'unknown'):
        block('genotype_unsupported', 'Genotype not supported by the model.')
    if patient.visits.filter(lab_sample_date__gt=lab_date).exists():
        block('not_latest_visit', 'A later visit exists. Predictions run on the latest visit only.')

    if age > 50:
        note = 'outside_training_age'
    elif patient.genotype in LOWER_RELIABILITY_GENOTYPES or age >= 18:
        note = 'lower_reliability'
    else:
        note = None
    return {
        'eligible': not reasons,
        'reasons': reasons,
        'reliability_note': note,
        'imputed_lab_count': sum(getattr(visit, lab) is None for lab in LABS),
    }


# ─── Patient analytics (requirements §2 summary block) ──────────────────────

PERIOD_DAYS = {'6m': 182, '12m': 365}


def analytics(patient, baseline, visits, period='all'):
    if period in PERIOD_DAYS:
        start = timezone.localdate() - timedelta(days=PERIOD_DAYS[period])
        visits = [v for v in visits if v.lab_sample_date >= start]

    hgbs = [num(v.hgb) for v in visits if v.hgb is not None]
    assessed = [v for v in visits if v.hgb is not None and baseline is not None
                and v.transfused_last_3_months == 'no'
                and (v.lab_sample_date - baseline.hu_start_date).days >= settings.MIN_DAYS_ON_HU]
    target = float(baseline.baseline_hgb) + settings.RESPONSE_THRESHOLD_GDL if baseline else None
    mcvs = [num(v.mcv) for v in visits if v.mcv is not None]
    dosed = [v for v in visits if v.dose_mg is not None]
    pains = [v.pain_episodes for v in visits if v.pain_episodes is not None]
    flags = list(SafetyFlag.objects.filter(visit__in=visits, status='active'))
    by_rule = {}
    for f in flags:
        by_rule[f.rule] = by_rule.get(f.rule, 0) + 1

    return {
        'visit_count': len(visits),
        'hgb': {
            'min': min(hgbs) if hgbs else None,
            'max': max(hgbs) if hgbs else None,
            'mean': round(sum(hgbs) / len(hgbs), 2) if hgbs else None,
            'visits_meeting_criterion': sum(float(v.hgb) >= target for v in assessed),
            'visits_assessed': len(assessed),
        },
        'mcv': {
            'first': mcvs[0] if mcvs else None,
            'latest': mcvs[-1] if mcvs else None,
            'change': round(mcvs[-1] - mcvs[0], 1) if len(mcvs) > 1 else None,
        },
        'dose': {
            'current_mg': dosed[-1].dose_mg if dosed else None,
            'current_mg_per_kg': dosed[-1].dose_mg_per_kg if dosed else None,
            'changes': sum(a.dose_mg != b.dose_mg for a, b in zip(dosed, dosed[1:])),
        },
        'adherence': {k: sum(v.adherence == k for v in visits) for k in ('good', 'partial', 'poor')},
        'toxicity': {
            'flags_total': len(flags),
            'critical': sum(f.severity == 'critical' for f in flags),
            'by_rule': by_rule,
        },
        'pain_episodes': {
            'total': sum(pains),
            'per_visit_avg': round(sum(pains) / len(pains), 1) if pains else None,
        },
        'transfusions_reported': sum(v.transfused_last_3_months == 'yes' for v in visits),
    }
