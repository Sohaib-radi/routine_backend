"""
Tracking API serializers. They validate shapes and render rows; every rule
lives in tracking.services / tracking.selectors.
"""

from rest_framework import serializers

from .. import selectors
from ..models import CheckIn, Pause, Schedule, StreakCache, Trackable


class OwnedPrimaryKeyField(serializers.PrimaryKeyRelatedField):
    """A primary key limited to the requesting user's objects (others: "does not exist")."""

    def __init__(self, selector, **kwargs):
        self.selector = selector
        kwargs.setdefault('pk_field', serializers.UUIDField())  # typed as uuid in the schema
        super().__init__(**kwargs)

    def get_queryset(self):
        return self.selector(self.context['request'].user)


# ----------------------------- trackables ---------------------------------- #
class ScheduleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Schedule
        fields = ['frequency', 'weekdays_mask', 'times_per_period', 'interval_days', 'rrule']


class StreakSerializer(serializers.ModelSerializer):
    """Cached streak. `computed_for_date` tells how fresh it is (the user's local date)."""

    class Meta:
        model = StreakCache
        fields = ['current', 'longest', 'completion_rate_30d', 'computed_for_date']


class TrackableSerializer(serializers.ModelSerializer):
    schedule = ScheduleSerializer(required=False, help_text='Defaults to daily.')
    streak = StreakSerializer(read_only=True, allow_null=True, help_text='Null until first computed.')

    class Meta:
        model = Trackable
        fields = [
            'id', 'name', 'domain', 'kind', 'icon', 'color', 'target_value', 'unit',
            'start_date', 'end_date', 'is_archived', 'config', 'schedule', 'streak', 'managed_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'is_archived', 'managed_by', 'created_at', 'updated_at']
        extra_kwargs = {'start_date': {'required': False, 'help_text': "Defaults to the user's today."}}

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if 'streak' in self.fields and not hasattr(instance, 'streak'):  # no cache row yet
            data['streak'] = None
        return data


class TrackableListQuerySerializer(serializers.Serializer):
    archived = serializers.BooleanField(required=False, default=False)
    domain = serializers.ChoiceField(choices=Trackable.Domain.choices, required=False)


# ----------------------------- check-ins ----------------------------------- #
class CheckInSerializer(serializers.ModelSerializer):
    trackable_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = CheckIn
        fields = ['id', 'trackable_id', 'date', 'status', 'value', 'note', 'source', 'created_at', 'updated_at']
        read_only_fields = fields


class CheckInLogSerializer(serializers.Serializer):
    """POST /checkins/: add to the day's check-in (repeated logs accumulate)."""

    trackable_id = OwnedPrimaryKeyField(selectors.trackables_for, source='trackable')
    date = serializers.DateField(required=False, help_text="Today or yesterday. Defaults to the user's today.")
    value = serializers.DecimalField(max_digits=10, decimal_places=2, required=False,
                                     help_text='Required for quantity/duration trackables; ignored for boolean.')
    note = serializers.CharField(required=False, allow_blank=True, default='')


class CheckInSkipSerializer(serializers.Serializer):
    trackable_id = OwnedPrimaryKeyField(selectors.trackables_for, source='trackable')
    date = serializers.DateField(required=False, help_text="Today or yesterday. Defaults to the user's today.")
    note = serializers.CharField(required=False, allow_blank=True, default='')


class CheckInValueSerializer(serializers.Serializer):
    """PATCH /checkins/{id}/: set the value (no accumulation)."""

    value = serializers.DecimalField(max_digits=10, decimal_places=2)


class HistoryQuerySerializer(serializers.Serializer):
    # `from` is a Python keyword: declared below.
    to = serializers.DateField(required=False, help_text="Defaults to the user's today.")

    def get_fields(self):
        fields = super().get_fields()
        fields['from'] = serializers.DateField(required=False, help_text='Defaults to 30 days before `to`.')
        return fields


# ----------------------------- pauses -------------------------------------- #
class PauseSerializer(serializers.ModelSerializer):
    trackable_id = OwnedPrimaryKeyField(
        selectors.trackables_for, source='trackable', required=False, allow_null=True,
        help_text='Null pauses everything.',
    )

    class Meta:
        model = Pause
        fields = ['id', 'trackable_id', 'start_date', 'end_date', 'reason', 'note', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'start_date': {'help_text': 'Yesterday at the earliest; future dates plan a pause.'},
            'end_date': {'help_text': 'Last paused day (inclusive). Null: until resumed.'},
        }


class PauseEndSerializer(serializers.Serializer):
    last_paused_day = serializers.DateField(required=False, help_text='Defaults to yesterday: today counts again.')


class PauseListQuerySerializer(serializers.Serializer):
    active = serializers.BooleanField(required=False, allow_null=True, default=None,
                                      help_text='true: running or planned; false: over.')


# ----------------------------- today --------------------------------------- #
class TodayTrackableSerializer(TrackableSerializer):
    class Meta(TrackableSerializer.Meta):
        fields = [f for f in TrackableSerializer.Meta.fields if f != 'streak']


class TodayItemSerializer(serializers.Serializer):
    trackable = TodayTrackableSerializer()
    checkin = CheckInSerializer(allow_null=True)
    streak = StreakSerializer(allow_null=True)


class TodaySerializer(serializers.Serializer):
    date = serializers.DateField(help_text="The user's local date.")
    items = TodayItemSerializer(many=True)
