from django.contrib import admin
from .models import HUBaseline, Prediction


@admin.register(HUBaseline)
class HUBaselineAdmin(admin.ModelAdmin):
    list_display = ('patient', 'baseline_hgb', 'hu_start_date')
    search_fields = ('patient__email',)


@admin.register(Prediction)
class PredictionAdmin(admin.ModelAdmin):
    list_display = ('patient', 'visit', 'predicted_class', 'response_probability', 'model_version', 'created_at')
    list_filter = ('predicted_class', 'model_version')
    search_fields = ('patient__email',)
    ordering = ('-created_at',)
