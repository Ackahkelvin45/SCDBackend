from rest_framework import serializers
from .models import Visit


class VisitSerializer(serializers.ModelSerializer):
    """Full serializer — used for POST (create) and GET /latest/."""

    class Meta:
        model = Visit
        fields = [
            'id',
            'visit_date',
            'hgb', 'wbc', 'platelet_count', 'hct', 'rbc', 'mcv', 'anc', 'arc',
            'toxicity_nausea', 'toxicity_vomiting', 'toxicity_rash',
            'toxicity_fatigue', 'toxicity_headache', 'toxicity_neutropenia',
            'pain_episodes', 'lab_photo_url',
            'predicted_class', 'status',
            'created_at',
        ]
        read_only_fields = ['id', 'predicted_class', 'status', 'created_at']


class VisitListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the history list."""

    class Meta:
        model = Visit
        fields = ['id', 'visit_date', 'hgb', 'wbc', 'pain_episodes', 'predicted_class', 'status']
