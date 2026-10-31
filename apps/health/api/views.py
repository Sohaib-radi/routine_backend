"""
Health API (/api/v1/health/). Thin: reads through health.selectors, writes through
health.services, always scoped to request.user.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tracking.dates import user_today

from .. import selectors, services
from .serializers import (
    CurrentPlanSerializer,
    HealthPlanSerializer,
    PlanInputSerializer,
    TargetsSerializer,
    WeightEntrySerializer,
)


def _inputs(request):
    serializer = PlanInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.to_inputs()


class PlanPreviewView(APIView):
    """Calculate the targets for these inputs ("Calculate" button). Saves nothing."""

    @extend_schema(request=PlanInputSerializer, responses=TargetsSerializer)
    def post(self, request):
        return Response(TargetsSerializer(services.preview_plan(_inputs(request))).data)


class PlanView(APIView):
    """The current plan (GET) and confirming a new one (POST, "Confirm" button)."""

    @extend_schema(responses=CurrentPlanSerializer)
    def get(self, request):
        plan = selectors.current_plan(request.user)
        if plan is None:
            raise NotFound('No plan yet.')
        return Response(CurrentPlanSerializer(plan).data)

    @extend_schema(request=PlanInputSerializer, responses={201: CurrentPlanSerializer})
    def post(self, request):
        """
        Save the plan, effective today: a new version (or today's version updated).
        Also records today's weight and updates the water/sleep/steps/activity trackables.
        """
        plan = services.confirm_plan(request.user, _inputs(request))
        return Response(CurrentPlanSerializer(plan).data, status=status.HTTP_201_CREATED)


class PlanHistoryView(generics.ListAPIView):
    """Every version of the plan, newest first."""

    serializer_class = HealthPlanSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):  # schema generation: no user
            return selectors.plan_history(None).none()
        return selectors.plan_history(self.request.user).select_related('user')


class WeightView(generics.ListAPIView):
    """Weight history, newest first (GET), and logging a weight (POST)."""

    serializer_class = WeightEntrySerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return selectors.weights_for(None).none()
        return selectors.weights_for(self.request.user)

    @extend_schema(request=WeightEntrySerializer, responses={200: WeightEntrySerializer})
    def post(self, request):
        """Record a weight. One per day: logging again that day replaces it."""
        serializer = WeightEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        entry = services.log_weight(request.user, data.get('date') or user_today(request.user), data['weight_kg'])
        return Response(WeightEntrySerializer(entry).data)
