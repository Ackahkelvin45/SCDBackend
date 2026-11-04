from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils.translation import gettext_lazy as _
from simple_history.admin import SimpleHistoryAdmin

from .models import CustomUser, Facility


@admin.register(Facility)
class FacilityAdmin(SimpleHistoryAdmin):
    list_display = ("name", "region", "prediction_enabled")
    list_editable = ("prediction_enabled",)
    search_fields = ("name", "region")


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ("email", "full_name", "facility", "license_number", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "facility")
    fieldsets = (
        (None, {"fields": ("email", "password")}),
        (_("Clinician"), {"fields": ("full_name", "phone_number", "license_number", "facility")}),
        (_("Permissions"), {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        (_("Important Dates"), {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "full_name", "phone_number", "license_number", "facility", "password1", "password2", "is_staff", "is_active"),
        }),
    )
    search_fields = ("email", "full_name", "license_number")
    ordering = ("email",)
