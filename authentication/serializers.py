from django.contrib.auth import get_user_model
from rest_framework import serializers
from django.contrib.auth.password_validation import validate_password
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
User = get_user_model()


class CustomTokenObtainPairSerializer(TokenObtainPairSerializer):
    pass


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ("patient_id", "email", "full_name", "date_of_birth", "gender", "phone_number", "password", "password2", "genotype")
        read_only_fields = ("patient_id",)

    # Mirror CustomUserManager.create_user so these return 400 instead of a 500
    def validate_password(self, value):
        if not any(char.isdigit() for char in value):
            raise serializers.ValidationError("Password must contain at least one digit")
        if not any(char.isalpha() for char in value):
            raise serializers.ValidationError("Password must contain at least one letter")
        return value

    def validate(self, attrs):
        if attrs["password"] != attrs["password2"]:
            raise serializers.ValidationError({"password": "Password fields didn't match."})
        return attrs
    
    def create(self, validated_data):
        validated_data.pop("password2")
        user = User.objects.create_user(**validated_data)
        return user
    


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("patient_id", "email", "full_name", "date_of_birth", "gender", "phone_number", "genotype")
        read_only_fields = ("patient_id",)