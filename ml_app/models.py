import uuid
from django.db import models
from django.conf import settings


class HUBaseline(models.Model):
    patient = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='hu_baseline',
    )
    baseline_hgb = models.DecimalField(max_digits=5, decimal_places=2)
    hu_start_date = models.DateField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f'{self.patient} — baseline {self.baseline_hgb} g/dL from {self.hu_start_date}'


class Prediction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    patient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='predictions',
    )
    visit = models.ForeignKey(
        'visits.Visit',
        on_delete=models.CASCADE,
        related_name='predictions',
    )
    response_probability = models.FloatField()
    predicted_class = models.CharField(max_length=20)  # 'responder' | 'non_responder'
    model_version = models.CharField(max_length=20, default='1.0')
    threshold = models.FloatField()
    threshold_type = models.CharField(max_length=20, default='Best F1')

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.patient} — {self.predicted_class} ({self.response_probability:.2f})'
