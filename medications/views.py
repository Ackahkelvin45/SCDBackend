from django.utils import timezone
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import MedicationLog
from .serializers import MedicationLogSerializer


class MedicationLogCreateView(generics.CreateAPIView):
    serializer_class = MedicationLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        # If a log already exists for this date, update it instead
        date = request.data.get('date') or timezone.localdate().isoformat()
        existing = MedicationLog.objects.filter(user=request.user, date=date).first()
        if existing:
            serializer = self.get_serializer(existing, data=request.data, partial=False)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return super().create(request, *args, **kwargs)


class MedicationTodayView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        today = timezone.localdate()
        log = MedicationLog.objects.filter(user=request.user, date=today).first()
        if not log:
            return Response({'logged': False, 'date': today.isoformat()}, status=status.HTTP_200_OK)
        serializer = MedicationLogSerializer(log)
        return Response({'logged': True, **serializer.data}, status=status.HTTP_200_OK)
