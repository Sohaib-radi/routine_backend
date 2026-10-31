"""
Tracking API (/api/v1/tracking/). Thin: every read goes through tracking.selectors
and every write through tracking.services, always scoped to request.user.
"""

from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from config.idempotency import SCHEMA_PARAMETER as IDEMPOTENCY_KEY
from config.idempotency import idempotent

from .. import selectors, services
from ..models import CheckIn, Pause, Trackable
from .serializers import (
    CheckInLogSerializer,
    CheckInSerializer,
    CheckInSkipSerializer,
    CheckInValueSerializer,
    HistoryQuerySerializer,
    PauseEndSerializer,
    PauseListQuerySerializer,
    PauseSerializer,
    TodaySerializer,
    TrackableListQuerySerializer,
    TrackableSerializer,
)


def _query(serializer_class, request):
    serializer = serializer_class(data=request.query_params)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


class TrackableViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                       mixins.UpdateModelMixin, viewsets.GenericViewSet):
    """The user's trackables. No DELETE: archive instead (check-ins are history)."""

    serializer_class = TrackableSerializer
    http_method_names = ['get', 'post', 'patch', 'head', 'options']

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):  # schema generation: no user
            return Trackable.objects.none()
        return selectors.trackables_for(self.request.user).order_by('created_at')

    @extend_schema(parameters=[TrackableListQuerySerializer])
    def list(self, request, *args, **kwargs):
        params = _query(TrackableListQuerySerializer, request)
        qs = self.get_queryset().filter(is_archived=params['archived'])
        if 'domain' in params:
            qs = qs.filter(domain=params['domain'])
        page = self.paginate_queryset(qs)
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def perform_create(self, serializer):
        serializer.instance = services.create_trackable(self.request.user, **serializer.validated_data)

    def perform_update(self, serializer):
        serializer.instance = services.update_trackable(
            self.request.user, serializer.instance, **serializer.validated_data
        )

    def _reloaded(self, trackable):
        return Response(self.get_serializer(self.get_queryset().get(pk=trackable.pk)).data)

    @extend_schema(request=None, responses=TrackableSerializer)
    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        return self._reloaded(services.archive_trackable(request.user, self.get_object()))

    @extend_schema(request=None, responses=TrackableSerializer)
    @action(detail=True, methods=['post'])
    def unarchive(self, request, pk=None):
        return self._reloaded(services.unarchive_trackable(request.user, self.get_object()))

    @extend_schema(parameters=[HistoryQuerySerializer], responses=CheckInSerializer(many=True))
    @action(detail=True, methods=['get'], pagination_class=None)
    def checkins(self, request, pk=None):
        """History of this trackable's check-ins, newest first (default: last 30 days, max 366)."""
        params = _query(HistoryQuerySerializer, request)
        rows = selectors.checkin_history(request.user, self.get_object(), params.get('from'), params.get('to'))
        return Response(CheckInSerializer(rows, many=True).data)


class TodayView(APIView):
    """The "Today" screen: trackables due today with today's check-in and cached streak."""

    @extend_schema(responses=TodaySerializer)
    def get(self, request):
        return Response(TodaySerializer(selectors.today_screen(request.user)).data)


class CheckInViewSet(viewsets.GenericViewSet):
    """
    Record and fix check-ins. POST adds to the day (accumulates); PATCH sets the
    value; DELETE removes it. Today and yesterday only (user's local date).
    """

    serializer_class = CheckInSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return CheckIn.objects.none()
        return selectors.checkins_for(self.request.user)

    @extend_schema(request=CheckInLogSerializer, responses={200: CheckInSerializer}, parameters=[IDEMPOTENCY_KEY])
    @idempotent
    def create(self, request):
        data = self._validated(CheckInLogSerializer, request)
        checkin = services.log_checkin(
            request.user, data['trackable'], data.get('date') or services.user_today(request.user),
            value=data.get('value'), source=CheckIn.Source.MANUAL, note=data['note'],
        )
        return Response(CheckInSerializer(checkin).data, status=status.HTTP_200_OK)

    @extend_schema(request=CheckInSkipSerializer, responses={200: CheckInSerializer}, parameters=[IDEMPOTENCY_KEY])
    @action(detail=False, methods=['post'])
    @idempotent
    def skip(self, request):
        """Consciously skip a day: it neither breaks nor extends the streak."""
        data = self._validated(CheckInSkipSerializer, request)
        checkin = services.skip_checkin(
            request.user, data['trackable'], data.get('date') or services.user_today(request.user),
            source=CheckIn.Source.MANUAL, note=data['note'],
        )
        return Response(CheckInSerializer(checkin).data, status=status.HTTP_200_OK)

    @extend_schema(request=CheckInValueSerializer, responses=CheckInSerializer)
    def partial_update(self, request, pk=None):
        """Fix a mistake: set the value directly (no accumulation)."""
        data = self._validated(CheckInValueSerializer, request)
        checkin = services.set_checkin_value(request.user, self.get_object(), data['value'])
        return Response(CheckInSerializer(checkin).data)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        """Fix a mistake: remove the check-in (or skip)."""
        services.delete_checkin(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)

    def _validated(self, serializer_class, request):
        serializer = serializer_class(data=request.data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data


class PauseViewSet(mixins.ListModelMixin, mixins.CreateModelMixin, mixins.RetrieveModelMixin,
                   viewsets.GenericViewSet):
    """Vacation / sickness / travel. Paused days neither break nor extend streaks."""

    serializer_class = PauseSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Pause.objects.none()
        return selectors.pauses_for(self.request.user)

    @extend_schema(parameters=[PauseListQuerySerializer])
    def list(self, request, *args, **kwargs):
        params = _query(PauseListQuerySerializer, request)
        page = self.paginate_queryset(selectors.pauses_for(request.user, active=params['active']))
        return self.get_paginated_response(self.get_serializer(page, many=True).data)

    def perform_create(self, serializer):
        serializer.instance = services.create_pause(self.request.user, **serializer.validated_data)

    @extend_schema(request=PauseEndSerializer, responses={200: PauseSerializer, 204: None})
    @action(detail=True, methods=['post'])
    def end(self, request, pk=None):
        """Resume (default: today counts again). Ending a pause that hasn't started cancels it (204)."""
        serializer = PauseEndSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        pause = services.end_pause(request.user, self.get_object(), serializer.validated_data.get('last_paused_day'))
        if pause is None:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response(self.get_serializer(pause).data)
