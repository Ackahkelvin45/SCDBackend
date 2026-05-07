from rest_framework import serializers
from .models import MedicationLog


class MedicationLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = MedicationLog
        fields = ['id', 'date', 'hu_taken', 'dose_mg', 'notes', 'logged_at']
        read_only_fields = ['id', 'logged_at']

    def validate(self, attrs):
        hu_taken = attrs.get('hu_taken', False)
        if hu_taken and not attrs.get('dose_mg'):
            raise serializers.ValidationError({'dose_mg': 'dose_mg is required when hu_taken is true.'})
        if not hu_taken:
            attrs['dose_mg'] = None
        return attrs
