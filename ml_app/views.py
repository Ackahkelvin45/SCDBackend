from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from visits.models import Visit
from .models import HUBaseline, Prediction
from .serializers import (
    HUBaselineSerializer,
    LatestPredictionSerializer,
    PredictErrorSerializer,
    PredictRequestSerializer,
    PredictResponseSerializer,
)
from . import predictor


class HUBaselineView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary='Get HU baseline',
        description=(
            'Returns the patient\'s Hydroxyurea baseline record — the haemoglobin value '
            'and start date recorded when HU was first initiated. Required before any '
            'prediction can be generated.'
        ),
        responses={
            200: HUBaselineSerializer,
            404: OpenApiResponse(description='No HU baseline recorded for this patient.'),
        },
    )
    def get(self, request):
        try:
            baseline = request.user.hu_baseline
        except HUBaseline.DoesNotExist:
            return Response(
                {'detail': 'No HU baseline recorded. Set your baseline_hgb and hu_start_date first.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(HUBaselineSerializer(baseline).data)

    @extend_schema(
        summary='Set or update HU baseline',
        description=(
            'Creates or replaces the patient\'s HU baseline. '
            'Only one baseline per patient is stored — posting again overwrites the previous record.'
        ),
        request=HUBaselineSerializer,
        responses={
            200: HUBaselineSerializer,
            400: OpenApiResponse(description='Validation error — see field errors in response body.'),
        },
    )
    def post(self, request):
        try:
            baseline = request.user.hu_baseline
            serializer = HUBaselineSerializer(baseline, data=request.data)
        except HUBaseline.DoesNotExist:
            serializer = HUBaselineSerializer(data=request.data)

        serializer.is_valid(raise_exception=True)
        serializer.save(patient=request.user)
        return Response(serializer.data, status=status.HTTP_200_OK)


class PredictView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary='Run HU response prediction',
        description=(
            'Accepts a visit ID and runs the XGBoost model to predict whether the patient '
            'will be a Hydroxyurea Responder at their next eligible clinic visit '
            '(defined as haemoglobin improvement ≥ 1.0 g/dL from HU baseline). '
            'The visit must belong to the authenticated patient and the patient must '
            'have at least 90 days on HU at the time of that visit. '
            'The prediction is saved and the visit status is updated automatically.'
        ),
        request=PredictRequestSerializer,
        responses={
            200: PredictResponseSerializer,
            400: OpenApiResponse(
                response=PredictErrorSerializer,
                description=(
                    'Bad request — either the HU baseline is not set, or the patient '
                    'has fewer than 90 days on HU at the visit date.'
                ),
            ),
            404: OpenApiResponse(description='Visit not found or does not belong to this patient.'),
            500: OpenApiResponse(description='Model inference failed — see detail field.'),
        },
    )
    def post(self, request):
        serializer = PredictRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        visit_id = serializer.validated_data['visit_id']

        try:
            visit = Visit.objects.get(id=visit_id, patient=request.user)
        except Visit.DoesNotExist:
            return Response({'detail': 'Visit not found.'}, status=status.HTTP_404_NOT_FOUND)

        try:
            hu_baseline = request.user.hu_baseline
        except HUBaseline.DoesNotExist:
            return Response(
                {'detail': 'HU baseline not set. Record your baseline_hgb and hu_start_date before predicting.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        days_on_hu = (visit.visit_date - hu_baseline.hu_start_date).days
        if days_on_hu < 90:
            return Response(
                {
                    'detail': f'Patient has only {days_on_hu} days on HU. Prediction requires at least 90 days on treatment.',
                    'days_on_hu': days_on_hu,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = predictor.predict(visit, hu_baseline)
        except Exception as exc:
            return Response(
                {'detail': f'Prediction failed: {str(exc)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        prediction = Prediction.objects.create(
            patient=request.user,
            visit=visit,
            response_probability=result['response_probability'],
            predicted_class=result['predicted_class'],
            model_version=result['model_version'],
            threshold=result['threshold'],
            threshold_type=result['threshold_type'],
        )

        visit.predicted_class = result['predicted_class']
        visit.save(update_fields=['predicted_class', 'status', 'updated_at'])

        return Response({
            'id': str(prediction.id),
            'visit_id': str(visit.id),
            'response_probability': result['response_probability'],
            'predicted_class': result['predicted_class'],
            'model_version': result['model_version'],
            'target_definition': result['target_definition'],
            'threshold': result['threshold'],
            'threshold_type': result['threshold_type'],
        })


class LatestPredictionView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        summary='Get latest prediction',
        description=(
            'Returns the most recent HU response prediction for the authenticated patient. '
            'Used by the Home dashboard risk card to display the current prediction status '
            'without re-running the model.'
        ),
        responses={
            200: LatestPredictionSerializer,
            404: OpenApiResponse(description='No predictions have been generated for this patient yet.'),
        },
    )
    def get(self, request):
        prediction = Prediction.objects.filter(patient=request.user).first()
        if not prediction:
            return Response(
                {'detail': 'No predictions found.'},
                status=status.HTTP_404_NOT_FOUND,
            )
        return Response(LatestPredictionSerializer(prediction).data)
