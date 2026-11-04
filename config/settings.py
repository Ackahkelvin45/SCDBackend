import os
import dj_database_url
from pathlib import Path
from datetime import timedelta

BASE_DIR = Path(__file__).resolve().parent.parent

DEBUG = os.environ.get("DEBUG", "False") == "True"

# Production must provide SECRET_KEY; the insecure fallback is for local DEBUG only
if DEBUG:
    SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-change-me-in-production")
else:
    SECRET_KEY = os.environ["SECRET_KEY"]

ALLOWED_HOSTS = ["*"]

CORS_ALLOW_ALL_ORIGINS = True

CSRF_TRUSTED_ORIGINS = ["https://mosquitosurveillancebackend.website"]


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    'authentication',
    'clinic',
    'simple_history',
    'rest_framework',
    'rest_framework.authtoken',
    'rest_framework_simplejwt.token_blacklist',
    'corsheaders',
    'drf_spectacular',
    ]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'EXCEPTION_HANDLER': 'config.errors.exception_handler',
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.LimitOffsetPagination',
    'PAGE_SIZE': 50,
    # Anonymous limit guards login/register against brute force
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '20/min',
        'user': '1000/hour',
    },
    # One nginx hop: identify clients by the IP nginx appends to X-Forwarded-For, not one they send
    'NUM_PROXIES': 1,
}


SPECTACULAR_SETTINGS = {
    'TITLE': 'SCD API',
    'DESCRIPTION': 'API documentation for SCD project',
    'VERSION': '1.0.0',
    'SERVE_INCLUDE_SCHEMA': False,
    'COMPONENT_SPLIT_REQUEST': True,
    'SWAGGER_UI_SETTINGS': {
        'deepLinking': True,
        'persistAuthorization': True,
    },      
}       
ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


SIMPLE_JWT = {
    # Token lifetimes
    "ACCESS_TOKEN_LIFETIME":  timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),

    # Rotation — issue a new refresh token on every refresh
    "ROTATE_REFRESH_TOKENS":  True,

    # Blacklist old refresh token after rotation
    "BLACKLIST_AFTER_ROTATION": True,

    # Algorithm & signing
    "ALGORITHM":               "HS256",
    "SIGNING_KEY":             SECRET_KEY,

    # Token type enforcement
    "AUTH_HEADER_TYPES":       ("Bearer",),
    "AUTH_HEADER_NAME":        "HTTP_AUTHORIZATION",

    # Custom claims — pulls from your CustomUser model
    "USER_ID_FIELD":           "id",
    "USER_ID_CLAIM":           "user_id",

}


WSGI_APPLICATION = "config.wsgi.application"

AUTH_USER_MODEL = "authentication.CustomUser"

# Database
# https://docs.djangoproject.com/en/5.2/ref/settings/#databases

DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", "sqlite:///db.sqlite3"),
        conn_max_age=600,
        conn_health_checks=True,
    )
}


# Password validation
# https://docs.djangoproject.com/en/5.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/5.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/5.2/howto/static-files/

FORCE_SCRIPT_NAME = "/scd"

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

# Default primary key field type
# https://docs.djangoproject.com/en/5.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Security — only enforced in production (DEBUG=False)
if not DEBUG:
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000

# Render terminates SSL at the load balancer — Django must not redirect
SECURE_SSL_REDIRECT = False
SILENCED_SYSTEM_CHECKS = ["security.W008"]

# Password reset email (Django's built-in views at /accounts/password_reset/).
# Without EMAIL_HOST the reset email is only printed to the container log.
if os.environ.get("EMAIL_HOST"):
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_HOST = os.environ["EMAIL_HOST"]
    EMAIL_PORT = int(os.environ.get("EMAIL_PORT", "587"))
    EMAIL_HOST_USER = os.environ.get("EMAIL_HOST_USER", "")
    EMAIL_HOST_PASSWORD = os.environ.get("EMAIL_HOST_PASSWORD", "")
    EMAIL_USE_TLS = os.environ.get("EMAIL_USE_TLS", "True") == "True"
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "SickleCare <no-reply@mosquitosurveillancebackend.website>")


# ─── Clinical tables (spec §5–§7). Served to the app by GET /config/ so both sides use the same values.
# [CONFIRM] ranges and flag thresholds with a haematologist; changing them here needs no code change.
LAB_RANGES = {
    "hgb": {"min": 3, "max": 16, "unit": "g/dL"},
    "wbc": {"min": 0.5, "max": 60, "unit": "×10⁹/L"},
    "anc": {"min": 0.1, "max": 40, "unit": "×10⁹/L"},
    "platelet_count": {"min": 10, "max": 1500, "unit": "×10⁹/L"},
    "rbc": {"min": 1, "max": 7, "unit": "×10¹²/L"},
    "mcv": {"min": 50, "max": 140, "unit": "fL"},
    "hct": {"min": 10, "max": 60, "unit": "%"},
    "arc": {"min": 5, "max": 800, "unit": "×10⁹/L"},
}

FLAG_THRESHOLDS = {
    "anc_critical": 1.0,     # ANC below → critical
    "hgb_critical": 4.5,     # Hb below → critical
    "platelets_low": 80,     # platelets below → warning
    "arc_low": 80,           # ARC below (and Hb below arc_low_hgb) → warning
    "arc_low_hgb": 9.0,
    "hgb_drop": 2.0,         # Hb fall vs previous visit ≥ → warning
}

RESPONSE_THRESHOLD_GDL = 1.0   # Hb rise over baseline that counts as a response
MIN_DAYS_ON_HU = 90

# Every predict request runs all of these (multi-model-prediction.md v2). Metrics: deployed models on the
# held-out test set at each model's Best F1 threshold (docs/deployed_eval/). The first is shown by default.
PREDICTION_MODELS = [
    {"id": "xgboost", "name": "XGBoost", "file": "xgboost.pkl", "threshold_key": "XGBoost",
     "test_metrics": {"auc": 0.871, "sensitivity": 0.77, "specificity": 0.80}},
    {"id": "svm", "name": "SVM", "file": "svm.pkl", "threshold_key": "SVM",
     "test_metrics": {"auc": 0.866, "sensitivity": 0.79, "specificity": 0.79}},
    {"id": "logistic_reg", "name": "Logistic Regression", "file": "logistic_reg.pkl", "threshold_key": "Logistic Reg",
     "test_metrics": {"auc": 0.865, "sensitivity": 0.63, "specificity": 0.90}},
]
