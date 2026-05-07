import uuid
from django.db import models
from django.conf import settings


class StatusChoices(models.TextChoices):
    STABLE = 'STABLE', 'Stable'
    MONITOR = 'MONITOR', 'Monitor'
    ELEVATED = 'ELEVATED', 'Elevated'


class Visit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='visits',
    )
    visit_date = models.DateField()

    # Lab values — nullable so each step can be skipped
    hgb = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    wbc = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    platelet_count = models.DecimalField(max_digits=7, decimal_places=2, null=True, blank=True)
    hct = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    rbc = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    mcv = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    anc = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    arc = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    # Toxicity / side-effect flags
    toxicity_nausea = models.BooleanField(default=False)
    toxicity_vomiting = models.BooleanField(default=False)
    toxicity_rash = models.BooleanField(default=False)
    toxicity_fatigue = models.BooleanField(default=False)
    toxicity_headache = models.BooleanField(default=False)
    toxicity_neutropenia = models.BooleanField(default=False)

    # Clinical assessment
    pain_episodes = models.IntegerField(default=0)
    lab_photo_url = models.URLField(null=True, blank=True)

    # Set by the ML app after prediction; null until then
    predicted_class = models.CharField(max_length=20, null=True, blank=True)

    status = models.CharField(
        max_length=10,
        choices=StatusChoices.choices,
        default=StatusChoices.STABLE,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-visit_date', '-created_at']

    def __str__(self):
        return f'{self.patient} — {self.visit_date}'

    def _compute_status(self):
        # Prediction result takes priority once ML has run
        if self.predicted_class == 'responder':
            return StatusChoices.STABLE
        if self.predicted_class == 'non_responder':
            return StatusChoices.ELEVATED

        # Hb-based heuristic before prediction is available
        if self.hgb is None:
            return StatusChoices.STABLE
        hgb = float(self.hgb)
        if hgb >= 9.0:
            return StatusChoices.STABLE
        if hgb >= 7.0:
            return StatusChoices.MONITOR
        return StatusChoices.ELEVATED

    def save(self, *args, **kwargs):
        self.status = self._compute_status()
        super().save(*args, **kwargs)
