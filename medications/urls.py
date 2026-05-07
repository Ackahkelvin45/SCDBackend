from django.urls import path
from .views import MedicationLogCreateView, MedicationTodayView

urlpatterns = [
    path('log/', MedicationLogCreateView.as_view(), name='medication-log'),
    path('today/', MedicationTodayView.as_view(), name='medication-today'),
]
