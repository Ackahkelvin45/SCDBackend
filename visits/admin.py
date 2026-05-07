from django.contrib import admin

from .models import Visit


@admin.register(Visit)
class VisitAdmin(admin.ModelAdmin):
    list_display = ['patient', 'visit_date', 'hgb', 'wbc', 'pain_episodes', 'status', 'created_at']
    list_filter = ['status']
    search_fields = ['patient__email', 'patient__full_name']
    ordering = ['-visit_date']
    readonly_fields = ['id', 'status', 'created_at', 'updated_at']
