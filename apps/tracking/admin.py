"""
Admin for the tracking engine (Unfold).

Check-ins, pauses and streak caches are read-only here: check-ins must go
through tracking.services.log_checkin (accumulation, streak recompute), pauses
never rewrite history, and StreakCache is derived data. Trackables and their
schedules/reminders can be edited for support, except the targets, dates and
schedule of a managed trackable (managed_by set): those belong to its app.
"""

from django.contrib import admin, messages
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin, StackedInline, TabularInline
from unfold.decorators import action

from .models import CheckIn, Pause, Reminder, Schedule, StreakCache, Trackable
from .services import MANAGED_FIELDS
from .tasks import recompute_streak


class ReadOnlyAdminMixin:
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


class ScheduleInline(StackedInline):
    model = Schedule
    can_delete = False
    extra = 0
    fields = ['frequency', 'weekdays_mask', 'times_per_period', 'interval_days', 'rrule']

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.managed_by:
            return False
        return super().has_change_permission(request, obj)


class ReminderInline(TabularInline):
    model = Reminder
    extra = 0
    fields = ['time_of_day', 'weekdays_mask', 'is_active']


@admin.register(Trackable)
class TrackableAdmin(ModelAdmin):
    list_display = ['name', 'user', 'domain', 'kind', 'start_date', 'end_date', 'is_archived']
    list_filter = ['domain', 'kind', 'is_archived']
    list_select_related = ['user']
    search_fields = ['name', 'user__email']
    autocomplete_fields = ['user']
    readonly_fields = ['managed_by', 'created_at', 'updated_at']
    inlines = [ScheduleInline, ReminderInline]

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj is not None and obj.managed_by:
            fields += sorted(MANAGED_FIELDS) + ['is_archived']
        return fields


@admin.register(CheckIn)
class CheckInAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['trackable', 'user', 'date', 'status', 'value', 'source']
    list_filter = ['status', 'source', 'date']
    list_select_related = ['trackable', 'user']
    search_fields = ['trackable__name', 'user__email']
    date_hierarchy = 'date'


@admin.register(Pause)
class PauseAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['user', 'trackable', 'start_date', 'end_date', 'reason']
    list_filter = ['reason']
    list_select_related = ['user', 'trackable']
    search_fields = ['user__email', 'trackable__name']


@admin.register(StreakCache)
class StreakCacheAdmin(ReadOnlyAdminMixin, ModelAdmin):
    list_display = ['trackable', 'current', 'longest', 'completion_rate_30d', 'computed_for_date']
    list_select_related = ['trackable']
    search_fields = ['trackable__name', 'trackable__user__email']
    actions = ['recompute']

    @action(description=_('Recompute from check-ins and pauses'))
    def recompute(self, request, queryset):
        ids = list(queryset.values_list('trackable_id', flat=True))
        for trackable_id in ids:
            recompute_streak.delay(str(trackable_id))
        messages.success(request, _('%d streak(s) queued for recompute.') % len(ids))
