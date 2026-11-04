import uuid
from django.db import models
from django.contrib.auth.models import BaseUserManager, PermissionsMixin, AbstractBaseUser
from django.utils import timezone
from simple_history.models import HistoricalRecords


class Facility(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, unique=True)
    region = models.CharField(max_length=100, blank=True)
    # Prediction stays off until the ML owner confirms the ARC unit and ethics approval is recorded
    prediction_enabled = models.BooleanField(default=False)

    history = HistoricalRecords()

    class Meta:
        verbose_name_plural = "Facilities"
        ordering = ["name"]

    def __str__(self):
        return self.name


class CustomUserManager(BaseUserManager):

    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("The Email field must be set")
        if not password:
            raise ValueError("The Password field must be set")
        if len(password) < 8:
            raise ValueError("Password must be at least 8 characters long")
        if not any(char.isdigit() for char in password):
            raise ValueError("Password must contain at least one digit")
        if not any(char.isalpha() for char in password):
            raise ValueError("Password must contain at least one letter")
        email = self.normalize_email(email)
        extra_fields.setdefault("is_active", True)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        extra_fields.setdefault("is_active", True)

        if extra_fields.get("is_staff") is not True:
            raise ValueError("Superuser must have is_staff=True.")
        if extra_fields.get("is_superuser") is not True:
            raise ValueError("Superuser must have is_superuser=True.")

        return self.create_user(email, password, **extra_fields)


class CustomUser(AbstractBaseUser, PermissionsMixin):
    """A clinician. Patients never log in; they are records in the clinic app."""
    email = models.EmailField(unique=True)
    full_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=20, blank=True)
    license_number = models.CharField(max_length=50, blank=True)
    # Null only for staff/admin accounts; clinicians always belong to a facility
    facility = models.ForeignKey(Facility, on_delete=models.PROTECT, null=True, blank=True, related_name="clinicians")
    # Deactivating takes effect at once: access tokens and refreshes are refused for inactive users
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    date_joined = models.DateTimeField(default=timezone.now)

    objects = CustomUserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    class Meta:
        verbose_name = "Clinician"
        verbose_name_plural = "Clinicians"
        ordering = ["-date_joined"]

    @property
    def status(self):
        return "active" if self.is_active else "deactivated"

    def __str__(self):
        return self.email
