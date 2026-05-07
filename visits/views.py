from datetime import timedelta

from django.utils import timezone
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import Visit
from .serializers import VisitListSerializer, VisitSerializer


class VisitViewSet(viewsets.ModelViewSet):
    permission_classes = [permissions.IsAuthenticated]
    http_method_names = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        return Visit.objects.filter(patient=self.request.user)

    def get_serializer_class(self):
        if self.action == 'list':
            return VisitListSerializer
        return VisitSerializer

    def perform_create(self, serializer):
        serializer.save(patient=self.request.user)

    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()

        try:
            limit = int(request.query_params['limit']) if 'limit' in request.query_params else None
            offset = int(request.query_params.get('offset', 0))
        except ValueError:
            return Response({'detail': 'limit and offset must be integers.'}, status=status.HTTP_400_BAD_REQUEST)

        total = queryset.count()
        page = queryset[offset:offset + limit] if limit is not None else queryset[offset:]

        serializer = self.get_serializer(page, many=True)
        return Response({'count': total, 'results': serializer.data})

    @action(detail=False, methods=['get'])
    def latest(self, request):
        visit = self.get_queryset().first()
        if not visit:
            return Response({'detail': 'No visits found.'}, status=status.HTTP_404_NOT_FOUND)
        return Response(VisitSerializer(visit).data)

    @action(detail=False, methods=['get'])
    def trends(self, request):
        try:
            days = max(1, int(request.query_params.get('days', 7)))
        except ValueError:
            days = 7

        today = timezone.localdate()
        start_date = today - timedelta(days=days - 1)

        visits = self.get_queryset().filter(
            visit_date__gte=start_date,
            visit_date__lte=today,
        )

        # Keep only the most recent visit per day
        visit_map = {}
        for v in visits:
            if v.visit_date not in visit_map:
                visit_map[v.visit_date] = v

        result = []
        for i in range(days):
            date = start_date + timedelta(days=i)
            v = visit_map.get(date)
            result.append({
                'date': date.isoformat(),
                'day_label': date.strftime('%a'),
                'pain_episodes': v.pain_episodes if v else None,
                'hgb': float(v.hgb) if v and v.hgb is not None else None,
                'has_visit': v is not None,
            })

        return Response({'days': result})
