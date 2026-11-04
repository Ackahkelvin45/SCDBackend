import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models
from simple_history.models import HistoricalRecords

USER = settings.AUTH_USER_MODEL


class Patient(models.Model):
    SEX = [('male', 'Male'), ('female', 'Female')]
    GENOTYPE = [('HbSS', 'HbSS'), ('HbSC', 'HbSC'), ('HbSβ0', 'HbSβ0'), ('HbSβ+', 'HbSβ+'),
                ('other', 'Other'), ('unknown', 'Unknown')]
    ANC_FLOORS = [(Decimal('2.00'), '2.0'), (Decimal('1.25'), '1.25')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    facility = models.ForeignKey('authentication.Facility', on_delete=models.PROTECT, related_name='patients')
    folder_number = models.CharField(max_length=50)
    full_name = models.CharField(max_length=255)
    date_of_birth = models.DateField()
    sex = models.CharField(max_length=10, choices=SEX)
    genotype = models.CharField(max_length=10, choices=GENOTYPE)
    phone_number = models.CharField(max_length=20, blank=True)
    next_of_kin_name = models.CharField(max_length=255, blank=True)
    next_of_kin_phone = models.CharField(max_length=20, blank=True)
    primary_clinician = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='primary_patients')

    data_consent = models.BooleanField(default=False)
    data_consent_date = models.DateField(null=True, blank=True)
    research_consent = models.BooleanField(default=False)
    research_consent_date = models.DateField(null=True, blank=True)

    anc_flag_floor = models.DecimalField(max_digits=4, decimal_places=2, choices=ANC_FLOORS, default=Decimal('2.00'))
    anc_flag_floor_reason = models.CharField(max_length=500, blank=True)
    archived = models.BooleanField(default=False)

    created_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name='+')
    updated_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ['full_name']
        constraints = [
            models.UniqueConstraint(fields=['facility', 'folder_number'], name='unique_folder_number_per_facility'),
        ]

    def __str__(self):
        return f'{self.full_name} ({self.folder_number})'


class HUBaseline(models.Model):
    SOURCE = [('measured_pre_hu', 'Measured pre-HU'), ('from_folder', 'From folder'), ('unknown', 'Unknown')]

    patient = models.OneToOneField(Patient, on_delete=models.CASCADE, related_name='baseline')
    baseline_hgb = models.DecimalField(max_digits=4, decimal_places=1)
    baseline_source = models.CharField(max_length=20, choices=SOURCE)
    hu_start_date = models.DateField()

    updated_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name='+')
    updated_at = models.DateTimeField(auto_now=True)

    # Every edit is a new version; predictions remember the version they used
    history = HistoricalRecords()

    @property
    def version_id(self):
        latest = self.history.first()
        return latest.history_id if latest else None

    def __str__(self):
        return f'{self.patient} — {self.baseline_hgb} g/dL from {self.hu_start_date}'


class Visit(models.Model):
    ADHERENCE = [('good', 'Good'), ('partial', 'Partial'), ('poor', 'Poor')]
    YES_NO_UNKNOWN = [('yes', 'Yes'), ('no', 'No'), ('unknown', 'Unknown')]
    PREGNANCY = [('not_pregnant', 'Not pregnant'), ('pregnant', 'Pregnant'), ('planning', 'Planning'), ('unknown', 'Unknown')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='visits')
    lab_sample_date = models.DateField()
    consult_date = models.DateField(null=True, blank=True)

    # Labs in canonical units (spec §5); the app converts before sending
    hgb = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    wbc = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    anc = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)
    platelet_count = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True)
    rbc = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    mcv = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    hct = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    arc = models.DecimalField(max_digits=6, decimal_places=1, null=True, blank=True)

    weight_kg = models.DecimalField(max_digits=5, decimal_places=1, null=True, blank=True)
    dose_mg = models.PositiveIntegerField(null=True, blank=True)
    adherence = models.CharField(max_length=10, choices=ADHERENCE, null=True, blank=True)

    transfused_last_3_months = models.CharField(max_length=10, choices=YES_NO_UNKNOWN)
    pregnancy_status = models.CharField(max_length=20, choices=PREGNANCY, null=True, blank=True)
    contraception_counselled = models.BooleanField(default=False)

    toxicity_nausea = models.BooleanField(default=False)
    toxicity_vomiting = models.BooleanField(default=False)
    toxicity_rash = models.BooleanField(default=False)
    toxicity_fatigue = models.BooleanField(default=False)
    toxicity_headache = models.BooleanField(default=False)
    pain_episodes = models.PositiveIntegerField(null=True, blank=True)
    next_appointment_date = models.DateField(null=True, blank=True)
    note = models.TextField(blank=True)

    edited = models.BooleanField(default=False)
    created_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name='+')
    updated_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ['-lab_sample_date', '-created_at']

    @property
    def dose_mg_per_kg(self):
        if self.dose_mg is None or not self.weight_kg:
            return None
        return round(self.dose_mg / float(self.weight_kg), 1)

    def __str__(self):
        return f'{self.patient} — {self.lab_sample_date}'


class SafetyFlag(models.Model):
    SEVERITY = [('critical', 'Critical'), ('warning', 'Warning'), ('info', 'Info')]
    STATUS = [('active', 'Active'), ('resolved_by_edit', 'Resolved by edit')]
    ACTIONS = [('hold_hu', 'Hold HU'), ('repeat_fbc', 'Repeat FBC'), ('refer', 'Refer'),
               ('no_action', 'No action'), ('other', 'Other')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name='flags')
    patient = models.ForeignKey(Patient, on_delete=models.CASCADE, related_name='flags')
    rule = models.CharField(max_length=30)
    severity = models.CharField(max_length=10, choices=SEVERITY)
    message = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS, default='active')
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    acknowledged_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    action = models.CharField(max_length=20, choices=ACTIONS, null=True, blank=True)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.rule} ({self.severity}) — {self.patient}'


class PredictionRun(models.Model):
    """One predict request: every registered model runs on the same visit (multi-model-prediction.md v2)."""
    STATUS = [('current', 'Current'), ('outdated', 'Outdated')]
    OUTCOME = [('confirmed', 'Confirmed'), ('not_confirmed', 'Not confirmed'), ('not_assessable', 'Not assessable')]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visit = models.ForeignKey(Visit, on_delete=models.CASCADE, related_name='prediction_runs')
    status = models.CharField(max_length=10, choices=STATUS, default='current')
    baseline_version_id = models.IntegerField(null=True)
    baseline_hgb = models.DecimalField(max_digits=4, decimal_places=1)  # the version used, for the outcome check
    imputed_lab_count = models.PositiveSmallIntegerField()
    reliability_note = models.CharField(max_length=30, null=True, blank=True)
    override_reason = models.TextField(blank=True)

    # Filled in automatically once the next visit is saved
    outcome = models.CharField(max_length=20, choices=OUTCOME, null=True, blank=True)
    outcome_visit = models.ForeignKey(Visit, on_delete=models.SET_NULL, null=True, blank=True, related_name='+')
    outcome_delta_hgb = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    created_by = models.ForeignKey(USER, on_delete=models.SET_NULL, null=True, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)

    history = HistoricalRecords()

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'Run {self.id} on {self.visit}'


class ModelPrediction(models.Model):
    run = models.ForeignKey(PredictionRun, on_delete=models.CASCADE, related_name='results')
    model = models.CharField(max_length=30)
    response_probability = models.FloatField()
    predicted_class = models.CharField(max_length=20)  # 'responder' | 'non_responder'
    threshold = models.FloatField()
    model_hash = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['run', 'model'], name='one_result_per_model_per_run')]

    def __str__(self):
        return f'{self.model}: {self.predicted_class} ({self.response_probability:.2f})'
