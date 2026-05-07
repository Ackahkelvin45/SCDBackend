from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from .models import HUBaseline, Prediction


class HUBaselineSerializer(serializers.ModelSerializer):
    class Meta:
        model = HUBaseline
        fields = ['id', 'baseline_hgb', 'hu_start_date', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class PredictRequestSerializer(serializers.Serializer):
    visit_id = serializers.UUIDField(
        help_text='UUID of the visit to run the prediction against.'
    )


class PredictResponseSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text='UUID of the saved Prediction record.')
    visit_id = serializers.UUIDField(help_text='UUID of the visit used for this prediction.')
    response_probability = serializers.FloatField(
        help_text='Probability (0–1) that the patient is a Responder at their next eligible visit.'
    )
    predicted_class = serializers.ChoiceField(
        choices=['responder', 'non_responder'],
        help_text='"responder" if probability >= threshold, otherwise "non_responder".',
    )
    model_version = serializers.CharField(help_text='Version string of the deployed model.')
    target_definition = serializers.CharField(
        help_text='Human-readable definition of the target variable.'
    )
    threshold = serializers.FloatField(
        help_text='Decision threshold applied to response_probability to produce predicted_class.'
    )
    threshold_type = serializers.CharField(
        help_text='Which threshold strategy was used — "Best F1" or "Sens ≥90%".'
    )


class LatestPredictionSerializer(serializers.ModelSerializer):
    target_definition = serializers.SerializerMethodField()
    visit_id = serializers.UUIDField(source='visit.id', read_only=True)

    class Meta:
        model = Prediction
        fields = [
            'id',
            'visit_id',
            'response_probability',
            'predicted_class',
            'model_version',
            'target_definition',
            'threshold',
            'threshold_type',
            'created_at',
        ]

    @extend_schema_field(serializers.CharField())
    def get_target_definition(self, obj):
        return 'hgb improvement >= 1.0 g/dL from HU baseline at next eligible visit'


class PredictErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    days_on_hu = serializers.IntegerField(required=False)
