"""
Read queries and pure computations of the tracking engine. Views, agents and
Celery tasks call these; nothing else re-implements "is it due?" or streak rules.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal

from dateutil.rrule import rrulestr
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Q

from .dates import user_today
from .models import CheckIn, Pause, Schedule, StreakCache, Trackable


def _days(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


# ----------------------------- pauses -------------------------------------- #
def paused_dates(user, trackable, start: date, end: date) -> set[date]:
    """All dates in [start, end] covered by a global or a trackable-specific pause of `user`."""
    if start > end:
        return set()
    pauses = Pause.objects.filter(
        Q(trackable__isnull=True) | Q(trackable=trackable),
        user=user,
        start_date__lte=end,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=start))

    result = set()
    for p in pauses:
        result.update(_days(max(p.start_date, start), min(p.end_date or end, end)))
    return result


def is_paused(user, trackable, d: date) -> bool:
    return bool(paused_dates(user, trackable, d, d))


# ----------------------------- schedule ------------------------------------ #
def scheduled_dates(schedule: Schedule, start: date, end: date) -> set[date]:
    """
    Dates in [start, end] on which the trackable is expected (ignoring pauses).
    TIMES_PER_WEEK / TIMES_PER_MONTH: every day is eligible; the target is per period.
    """
    t = schedule.trackable
    start = max(start, t.start_date)
    if t.end_date:
        end = min(end, t.end_date)
    if start > end:
        return set()

    f = schedule.frequency
    if f == Schedule.Frequency.WEEKDAYS:
        return {d for d in _days(start, end) if schedule.weekdays_mask & (1 << d.weekday())}
    if f == Schedule.Frequency.EVERY_N_DAYS:
        n = schedule.interval_days or 1
        return {d for d in _days(start, end) if (d - t.start_date).days % n == 0}
    if f == Schedule.Frequency.RRULE:
        # Parsed once per range, not once per day. dtstart anchors COUNT/INTERVAL rules.
        rule = rrulestr(schedule.rrule, dtstart=datetime.combine(t.start_date, datetime.min.time()))
        lo, hi = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.max.time())
        return {occurrence.date() for occurrence in rule.between(lo, hi, inc=True)}
    # DAILY, TIMES_PER_WEEK, TIMES_PER_MONTH
    return set(_days(start, end))


def is_scheduled(schedule: Schedule, d: date) -> bool:
    """Is the trackable expected on date d (ignoring pauses)?"""
    return d in scheduled_dates(schedule, d, d)


def trackables_for(user):
    """The user's trackables (always scoped to `user`)."""
    return Trackable.objects.filter(user=user).select_related('schedule', 'streak')


def _paused_today(user, today: date) -> tuple[bool, set]:
    """One query: (is everything paused?, ids of trackables paused individually) on `today`."""
    scopes = set(
        Pause.objects.filter(Q(end_date__isnull=True) | Q(end_date__gte=today), user=user, start_date__lte=today)
        .values_list('trackable_id', flat=True)
    )
    return None in scopes, scopes - {None}


def _due(user, today: date, trackables) -> list[Trackable]:
    everything_paused, paused_ids = _paused_today(user, today)
    if everything_paused:
        return []
    return [t for t in trackables if t.pk not in paused_ids and is_scheduled(t.schedule, today)]


def due_today(user) -> list[Trackable]:
    """Trackables to show on the user's 'Today' screen, in the user's local date."""
    return _due(user, user_today(user), trackables_for(user).filter(is_archived=False))


@dataclass(frozen=True)
class TodayItem:
    trackable: Trackable
    checkin: CheckIn | None
    streak: StreakCache | None


@dataclass(frozen=True)
class TodayScreen:
    date: date
    items: list[TodayItem]


def today_screen(user) -> TodayScreen:
    """
    The "Today" screen: due trackables, today's check-in and cached streak for each.
    Three queries whatever the number of trackables: trackables (+schedule, +streak),
    today's pauses, today's check-ins.
    """
    today = user_today(user)
    due = _due(user, today, trackables_for(user).filter(is_archived=False).order_by('created_at'))
    checkins = {
        c.trackable_id: c
        for c in CheckIn.objects.filter(user=user, date=today, trackable__in=[t.pk for t in due])
    } if due else {}
    return TodayScreen(
        date=today,
        items=[TodayItem(t, checkins.get(t.pk), getattr(t, 'streak', None)) for t in due],
    )


# ----------------------------- check-ins & pauses -------------------------- #
HISTORY_DEFAULT_DAYS = 30
HISTORY_MAX_DAYS = 366


def checkins_for(user):
    """The user's check-ins (always scoped to `user`)."""
    return CheckIn.objects.filter(user=user)


def checkin_history(user, trackable, start: date | None = None, end: date | None = None):
    """
    Check-ins of one of the user's trackables in [start, end], newest first.
    Defaults to the last 30 days; at most 366 days per request.
    """
    end = end or user_today(user)
    start = start or end - timedelta(days=HISTORY_DEFAULT_DAYS - 1)
    if start > end:
        raise ValidationError({'from': 'Must be on or before `to`.'})
    if (end - start).days + 1 > HISTORY_MAX_DAYS:
        raise ValidationError({'from': f'At most {HISTORY_MAX_DAYS} days per request.'})
    return checkins_for(user).filter(trackable=trackable, date__gte=start, date__lte=end).order_by('-date')


def pauses_for(user, *, active: bool | None = None):
    """
    The user's pauses, newest first. active=True: running or planned (not over);
    active=False: over.
    """
    qs = Pause.objects.filter(user=user).select_related('trackable').order_by('-start_date', '-created_at')
    if active is None:
        return qs
    today = user_today(user)
    current = Q(end_date__isnull=True) | Q(end_date__gte=today)
    return qs.filter(current) if active else qs.exclude(current)


# ----------------------------- new local day ------------------------------- #
def timezones_with_new_day(start: datetime, end: datetime) -> list[str]:
    """
    The users' timezones whose local date changed between the instants `start` and
    `end` (UTC-aware): their midnight passed in that interval. Compares dates rather
    than looking for "hour 0", so DST days where local midnight is skipped (e.g.
    Cairo on 2026-04-24, 23:00 -> 01:00) or repeated are each caught exactly once.
    """
    zones = get_user_model().objects.values_list('timezone', flat=True).distinct()
    return sorted(
        tz for tz in zones
        if start.astimezone(ZoneInfo(tz)).date() != end.astimezone(ZoneInfo(tz)).date()
    )


def trackable_ids_for_new_day(start: datetime, end: datetime) -> list:
    """Non-archived trackables of users whose local midnight passed in (start, end]."""
    zones = timezones_with_new_day(start, end)
    if not zones:
        return []
    return list(
        Trackable.objects.filter(user__timezone__in=zones, user__is_active=True, is_archived=False)
        .order_by('pk').values_list('pk', flat=True)
    )


# ----------------------------- streaks ------------------------------------- #
@dataclass(frozen=True)
class StreakStats:
    current: int
    longest: int
    completion_rate_30d: Decimal  # percent, 0-100


PER_PERIOD_FREQUENCIES = {Schedule.Frequency.TIMES_PER_WEEK, Schedule.Frequency.TIMES_PER_MONTH}


def compute_streak(trackable: Trackable, today: date) -> StreakStats:
    """
    Rebuild streak statistics from the source data (CheckIn + Pause + Schedule).

    Walks every day from the trackable's start to `today`:
    - paused, not scheduled and skipped days are neutral: they neither break nor extend;
    - a done day extends the run;
    - any other scheduled day in the past breaks it;
    - today, if not done yet, is neutral (the day isn't over).

    TODO: TIMES_PER_WEEK / TIMES_PER_MONTH need a per-period variant (a week counts
    when `times_per_period` check-ins are done). Until then they report zeros.
    TODO: per-trackable skip limit (e.g. "at most 1 skip per week"); today every
    skip is neutral.
    """
    schedule = trackable.schedule
    if schedule.frequency in PER_PERIOD_FREQUENCIES:
        return StreakStats(0, 0, Decimal('0.00'))

    start = trackable.start_date
    end = min(today, trackable.end_date) if trackable.end_date else today
    if start > end:
        return StreakStats(0, 0, Decimal('0.00'))

    statuses = dict(
        CheckIn.objects.filter(trackable=trackable, date__gte=start, date__lte=end)
        .values_list('date', 'status')
    )
    paused = paused_dates(trackable.user, trackable, start, end)
    scheduled = scheduled_dates(schedule, start, end)

    run = longest = 0
    window_start = today - timedelta(days=29)
    expected_30d = done_30d = 0
    for d in _days(start, end):
        status = statuses.get(d)
        if d in paused or d not in scheduled or status == CheckIn.Status.SKIPPED:
            continue
        if status == CheckIn.Status.DONE:
            run += 1
            longest = max(longest, run)
        elif d != today:
            run = 0
        # completion rate: scheduled, unpaused, unskipped days of the last 30;
        # today counts only once it is done.
        if d >= window_start and (d != today or status == CheckIn.Status.DONE):
            expected_30d += 1
            done_30d += status == CheckIn.Status.DONE

    rate = Decimal(done_30d * 100) / expected_30d if expected_30d else Decimal(0)
    return StreakStats(current=run, longest=longest, completion_rate_30d=rate.quantize(Decimal('0.01')))
