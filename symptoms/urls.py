from django.urls import path
from .views import SymptomLogCreateView

urlpatterns = [
    path('', SymptomLogCreateView.as_view(), name='symptom-log'),
]
