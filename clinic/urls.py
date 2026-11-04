from django.urls import path

from . import views

urlpatterns = [
    path('facilities/', views.FacilityListView.as_view(), name='facilities'),
    path('clinicians/', views.ClinicianListView.as_view(), name='clinicians'),
    path('config/', views.ConfigView.as_view(), name='config'),

    path('patients/', views.PatientListView.as_view(), name='patients'),
    path('patients/<uuid:pk>/', views.PatientDetailView.as_view(), name='patient-detail'),
    path('patients/<uuid:pk>/hu-baseline/', views.BaselineView.as_view(), name='patient-baseline'),
    path('patients/<uuid:pk>/hu-baseline/history/', views.BaselineHistoryView.as_view(), name='patient-baseline-history'),
    path('patients/<uuid:pk>/visits/', views.PatientVisitsView.as_view(), name='patient-visits'),
    path('patients/<uuid:pk>/trends/', views.TrendsView.as_view(), name='patient-trends'),
    path('patients/<uuid:pk>/predictions/', views.PatientPredictionsView.as_view(), name='patient-predictions'),

    path('visits/<uuid:pk>/', views.VisitDetailView.as_view(), name='visit-detail'),
    path('visits/<uuid:pk>/prediction-eligibility/', views.EligibilityView.as_view(), name='visit-eligibility'),
    path('visits/<uuid:pk>/predict/', views.PredictView.as_view(), name='visit-predict'),

    path('flags/', views.FlagListView.as_view(), name='flags'),
    path('flags/<uuid:pk>/acknowledge/', views.FlagAcknowledgeView.as_view(), name='flag-acknowledge'),
]
