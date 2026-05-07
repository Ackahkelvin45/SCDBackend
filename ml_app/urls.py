from django.urls import path
from .views import HUBaselineView, LatestPredictionView, PredictView

urlpatterns = [
    path('predict-hu-response/', PredictView.as_view(), name='ml-predict'),
    path('predictions/latest/', LatestPredictionView.as_view(), name='ml-predictions-latest'),
    path('hu-baseline/', HUBaselineView.as_view(), name='ml-hu-baseline'),
]
