from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from rest_framework import serializers

from .models import Facility

User = get_user_model()


def clinician_data(user):
    """The Clinician shape the app uses for /auth/users/me/, sign-up and the login response."""
    facility = user.facility
    return {
        "id": str(user.pk),
        "full_name": user.full_name,
        "email": user.email,
        "phone_number": user.phone_number,
        "license_number": user.license_number,
        "status": user.status,
        "facility": None if facility is None else {
            "id": str(facility.pk), "name": facility.name, "region": facility.region,
            "prediction_enabled": facility.prediction_enabled,
        },
    }


def check_password_rules(value):
    # Mirror CustomUserManager.create_user so these return 400 instead of a 500
    if not any(char.isdigit() for char in value):
        raise serializers.ValidationError("Password must contain at least one digit")
    if not any(char.isalpha() for char in value):
        raise serializers.ValidationError("Password must contain at least one letter")
    return value


class RegisterSerializer(serializers.ModelSerializer):
    facility_id = serializers.PrimaryKeyRelatedField(source="facility", queryset=Facility.objects.all())
    password = serializers.CharField(write_only=True, validators=[validate_password, check_password_rules])
    password2 = serializers.CharField(write_only=True)

    class Meta:
        model = User
        fields = ("full_name", "email", "phone_number", "license_number", "facility_id", "password", "password2")
        extra_kwargs = {f: {"required": True, "allow_blank": False} for f in ("full_name", "phone_number", "license_number")}

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("An account with this email already exists.")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError({"password2": "Password fields didn't match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop("password2")
        return User.objects.create_user(**validated_data)


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("full_name", "phone_number")
        extra_kwargs = {"full_name": {"allow_blank": False}}


class PasswordChangeSerializer(serializers.Serializer):
    old_password = serializers.CharField()
    new_password = serializers.CharField(validators=[check_password_rules])
    new_password2 = serializers.CharField()

    def validate_old_password(self, value):
        if not self.context["user"].check_password(value):
            raise serializers.ValidationError("Your current password is incorrect.")
        return value

    def validate(self, attrs):
        if attrs["new_password"] != attrs["new_password2"]:
            raise serializers.ValidationError({"new_password2": "Password fields didn't match."})
        try:
            validate_password(attrs["new_password"], self.context["user"])
        except Exception as exc:
            raise serializers.ValidationError({"new_password": list(getattr(exc, "messages", [str(exc)]))})
        return attrs
