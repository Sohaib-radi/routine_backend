"""
AI API (/api/v1/ai/). Thin: services and selectors only, scoped to request.user.
Every endpoint answers 503 "ai_disabled" when the feature is off in the admin;
manual logging (health) is never affected.
"""

from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response

from apps.health import selectors as health_selectors
from apps.health.api.serializers import MealSerializer
from config.idempotency import SCHEMA_PARAMETER as IDEMPOTENCY_KEY
from config.idempotency import idempotent

from .. import config, selectors, services
from ..errors import AIDisabled, DailyLimitExceeded
from ..models import Feature, MealDraft
from .serializers import (
    AIErrorSerializer,
    DailyLimitErrorSerializer,
    MealDraftCreateSerializer,
    MealDraftSerializer,
    MealDraftUpdateSerializer,
)

DISABLED = OpenApiResponse(AIErrorSerializer, description='The feature is off: code "ai_disabled".')


class MealDraftViewSet(viewsets.GenericViewSet):
    """
    Log a meal from text ("اكتب أكلك"): POST the text (202), poll GET until `ready`,
    PATCH the items, then POST confirm. Nothing is saved to meals before confirm.
    """

    serializer_class = MealDraftSerializer
    feature = Feature.MEAL_TEXT

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return MealDraft.objects.none()
        return selectors.drafts_for(self.request.user)

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not config.feature_config(self.feature).enabled:
            raise AIDisabled()

    def handle_exception(self, exc):
        if isinstance(exc, AIDisabled):
            return Response({'code': exc.code, 'detail': 'This AI feature is turned off. You can still log '
                                                         'meals manually.'},
                            status=status.HTTP_503_SERVICE_UNAVAILABLE)
        if isinstance(exc, DailyLimitExceeded):
            retry_after = max(1, int((exc.reset_at - timezone.now()).total_seconds()))
            return Response({'code': exc.code, 'detail': str(exc), 'limit': exc.limit,
                             'reset_at': exc.reset_at.isoformat()},
                            status=status.HTTP_429_TOO_MANY_REQUESTS, headers={'Retry-After': str(retry_after)})
        return super().handle_exception(exc)

    def _draft(self, pk):
        draft = selectors.draft_for(self.request.user, pk)
        if draft is None:
            raise NotFound()
        return draft

    @extend_schema(request=MealDraftCreateSerializer, parameters=[IDEMPOTENCY_KEY],
                   responses={202: MealDraftSerializer, 429: DailyLimitErrorSerializer, 503: DISABLED})
    @idempotent
    def create(self, request):
        """Start the analysis (in the background). Poll GET meal-drafts/{id}/ until status is ready or failed."""
        serializer = MealDraftCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        draft = services.create_draft(request.user, **serializer.validated_data)
        return Response(MealDraftSerializer(draft).data, status=status.HTTP_202_ACCEPTED)

    @extend_schema(responses={200: MealDraftSerializer, 503: DISABLED})
    def retrieve(self, request, pk=None):
        return Response(MealDraftSerializer(self._draft(pk)).data)

    @extend_schema(request=MealDraftUpdateSerializer, responses={200: MealDraftSerializer, 503: DISABLED})
    def partial_update(self, request, pk=None):
        """The user's edits: the full item list, the date, the meal type. Only while ready."""
        serializer = MealDraftUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        draft = services.update_draft(request.user, self._draft(pk), **serializer.to_service_kwargs())
        return Response(MealDraftSerializer(draft).data)

    @extend_schema(request=None, parameters=[IDEMPOTENCY_KEY],
                   responses={201: MealSerializer, 200: MealSerializer, 503: DISABLED})
    @action(detail=True, methods=['post'])
    @idempotent
    def confirm(self, request, pk=None):
        """Save the meal (source=agent), like a manual meal. 201 the first time, 200 when already confirmed."""
        draft = self._draft(pk)
        already = draft.status == MealDraft.Status.CONFIRMED
        meal = services.confirm_draft(request.user, draft)
        meal = health_selectors.meals_for(request.user, meal.date).get(pk=meal.pk)
        return Response(MealSerializer(meal).data, status=status.HTTP_200_OK if already else status.HTTP_201_CREATED)
