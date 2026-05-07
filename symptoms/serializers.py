from rest_framework import serializers
from .models import SymptomLog


class SymptomLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = SymptomLog
        fields = [
            'id',
            'logged_at',
            'pain_level',
            'pain_location',
            'stress_level',
            'hydration_level',
            'fatigue',
            'shortness_of_breath',
            'dizziness',
            'fever',
            'jaundice',
            'swelling',
        ]
        read_only_fields = ['id', 'logged_at']
