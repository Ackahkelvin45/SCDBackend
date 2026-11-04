import csv

from django.contrib import admin
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.http import HttpResponse
from simple_history.admin import SimpleHistoryAdmin

from .models import HUBaseline, ModelPrediction, Patient, PredictionRun, SafetyFlag, Visit


@admin.register(Patient)
class PatientAdmin(SimpleHistoryAdmin):
    # Reassigning a patient = changing primary_clinician here
    list_display = ("full_name", "folder_number", "facility", "primary_clinician", "genotype", "research_consent", "archived")
    list_filter = ("facility", "genotype", "research_consent", "archived")
    search_fields = ("full_name", "folder_number")


@admin.register(HUBaseline)
class HUBaselineAdmin(SimpleHistoryAdmin):
    list_display = ("patient", "baseline_hgb", "baseline_source", "hu_start_date", "updated_at")


@admin.register(Visit)
class VisitAdmin(SimpleHistoryAdmin):
    list_display = ("patient", "lab_sample_date", "hgb", "anc", "platelet_count", "edited")
    list_filter = ("patient__facility",)
    search_fields = ("patient__full_name", "patient__folder_number")


@admin.register(SafetyFlag)
class SafetyFlagAdmin(SimpleHistoryAdmin):
    list_display = ("patient", "rule", "severity", "status", "acknowledged_at", "action")
    list_filter = ("severity", "status", "rule")


class ModelPredictionInline(admin.TabularInline):
    model = ModelPrediction
    extra = 0
    readonly_fields = ("model", "response_probability", "predicted_class", "threshold", "model_hash")


@admin.action(description="Export selected runs to CSV (research)")
def export_csv(modeladmin, request, queryset):
    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="sicklecare_predictions.csv"'
    writer = csv.writer(response)
    writer.writerow(["run_id", "facility", "patient_folder_number", "visit_id", "lab_sample_date", "status",
                     "baseline_hgb_used", "imputed_lab_count", "reliability_note", "model", "response_probability",
                     "predicted_class", "threshold", "model_hash", "outcome", "outcome_delta_hgb", "correct", "created_at"])
    rows = 0
    for run in queryset.select_related("visit__patient__facility").prefetch_related("results"):
        patient = run.visit.patient
        for r in run.results.all():
            correct = "" if run.outcome not in ("confirmed", "not_confirmed") else \
                (r.predicted_class == "responder") == (run.outcome == "confirmed")
            writer.writerow([run.pk, patient.facility.name, patient.folder_number, run.visit_id, run.visit.lab_sample_date,
                             run.status, run.baseline_hgb, run.imputed_lab_count, run.reliability_note or "", r.model,
                             r.response_probability, r.predicted_class, r.threshold, r.model_hash, run.outcome or "",
                             run.outcome_delta_hgb if run.outcome_delta_hgb is not None else "", correct, run.created_at])
            rows += 1
    # Exports of patient data are always logged (requirements §7)
    LogEntry.objects.create(
        user=request.user, content_type=ContentType.objects.get_for_model(PredictionRun),
        object_repr="CSV export", action_flag=CHANGE,
        change_message=f"Exported {rows} predictions from {queryset.count()} runs to CSV")
    return response


@admin.register(PredictionRun)
class PredictionRunAdmin(SimpleHistoryAdmin):
    list_display = ("visit", "status", "outcome", "created_by", "created_at")
    list_filter = ("status", "outcome", "visit__patient__facility")
    inlines = [ModelPredictionInline]
    actions = [export_csv]
