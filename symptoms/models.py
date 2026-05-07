from django.db import models
from django.conf import settings
from django.core.validators import MinValueValidator, MaxValueValidator


class PainLocation(models.TextChoices):
    CHEST = 'chest', 'Chest'
    ABDOMEN = 'abdomen', 'Abdomen'
    BACK = 'back', 'Back'
    JOINTS = 'joints', 'Joints'
    HEAD = 'head', 'Head'
    OTHER = 'other', 'Other'


class SymptomLog(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='symptom_logs',
    )
    logged_at = models.DateTimeField(auto_now_add=True)

    # Scales 1–10
    pain_level = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    stress_level = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    hydration_level = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )

    pain_location = models.CharField(
        max_length=20,
        choices=PainLocation.choices,
    )

    # 6 symptom toggles
    fatigue = models.BooleanField(default=False)
    shortness_of_breath = models.BooleanField(default=False)
    dizziness = models.BooleanField(default=False)
    fever = models.BooleanField(default=False)
    jaundice = models.BooleanField(default=False)
    swelling = models.BooleanField(default=False)

    class Meta:
        ordering = ['-logged_at']

    def __str__(self):
        return f'{self.user} — pain {self.pain_level}/10 @ {self.logged_at:%Y-%m-%d %H:%M}'
