from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import UserViewSet,LoginView,LogoutView
from rest_framework_simplejwt.views import TokenRefreshView,TokenObtainPairView


router = DefaultRouter()
router.register(r"users", UserViewSet, basename="auth")

urlpatterns = [
    path("login/", LoginView.as_view(), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("", include(router.urls)),
]