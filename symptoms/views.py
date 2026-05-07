from rest_framework import generics, permissions
from .models import SymptomLog
from .serializers import SymptomLogSerializer


class SymptomLogCreateView(generics.CreateAPIView):
    serializer_class = SymptomLogSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)
