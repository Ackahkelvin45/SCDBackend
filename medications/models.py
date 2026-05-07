from django.db import models
from django.conf import settings
from django.utils import timezone


class MedicationLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='medication_logs',
    )
    date = models.DateField(default=timezone.localdate)
    hu_taken = models.BooleanField()
    dose_mg = models.PositiveIntegerField(null=True, blank=True)
    notes = models.CharField(max_length=500, blank=True)
    logged_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-logged_at']
        # one log entry per user per day
        constraints = [
            models.UniqueConstraint(fields=['user', 'date'], name='unique_medication_log_per_day'),
        ]

    def __str__(self):
        taken = 'taken' if self.hu_taken else 'skipped'
        return f'{self.user} — HU {taken} on {self.date}'
