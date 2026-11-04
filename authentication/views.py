from django.contrib.auth import get_user_model
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from config.errors import ApiError
from .serializers import MeSerializer, PasswordChangeSerializer, RegisterSerializer, clinician_data

User = get_user_model()


class RegisterView(APIView):
    """Clinician sign-up. The account is active straight away; the app signs in next."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(clinician_data(serializer.save()), status=status.HTTP_201_CREATED)


class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get_authenticate_header(self, request):
        return "Bearer"  # keeps a wrong password a 401 (DRF turns it into 403 when no authenticator is set)

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        email = str(data.get("email", "")).strip().lower()
        password = str(data.get("password", ""))
        user = User.objects.filter(email__iexact=email).first()
        # Only reveal deactivation to someone who knows the password
        if user and not user.is_active and user.check_password(password):
            raise ApiError(403, "account_deactivated", "This account has been deactivated. Contact your facility admin.")
        serializer = TokenObtainPairSerializer(data={"email": user.email if user else email, "password": password})
        serializer.is_valid(raise_exception=True)
        return Response({**serializer.validated_data, "user": clinician_data(serializer.user)})


class LogoutView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        data = request.data if isinstance(request.data, dict) else {}
        try:
            token = RefreshToken(data["refresh"])
            token.blacklist()
            return Response(status=status.HTTP_205_RESET_CONTENT)
        except KeyError:
            raise ApiError(400, "invalid", "Refresh token is required.")
        except TokenError:
            raise ApiError(400, "token_not_valid", "Token is invalid or expired.")


class MeView(APIView):
    def get(self, request):
        return Response(clinician_data(request.user))

    def patch(self, request):
        serializer = MeSerializer(request.user, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(clinician_data(request.user))


class PasswordChangeView(APIView):
    def post(self, request):
        serializer = PasswordChangeSerializer(data=request.data, context={"user": request.user})
        serializer.is_valid(raise_exception=True)
        request.user.set_password(serializer.validated_data["new_password"])
        request.user.save(update_fields=["password"])
        # End other sessions: their refresh tokens stop working (access tokens live out their short lifetime)
        for token in OutstandingToken.objects.filter(user=request.user):
            BlacklistedToken.objects.get_or_create(token=token)
        return Response({"detail": "Password changed."})
