"""
Read queries and pure computations of the tracking engine. Views, agents and
Celery tasks call these; nothing else re-implements "is it due?" or streak rules.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from dateutil.rrule import rrulestr
from django.db.models import Q

from .dates import user_today
from .models import CheckIn, Pause, Schedule, Trackable


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
    return Trackable.objects.filter(user=user).select_related('schedule')


def due_today(user) -> list[Trackable]:
    """Trackables to show on the user's 'Today' screen, in the user's local date."""
    today = user_today(user)
    trackables = list(trackables_for(user).filter(is_archived=False))
    global_paused = Pause.objects.filter(
        Q(end_date__isnull=True) | Q(end_date__gte=today),
        user=user, trackable__isnull=True, start_date__lte=today,
    ).exists()
    if global_paused:
        return []
    paused_ids = set(
        Pause.objects.filter(
            Q(end_date__isnull=True) | Q(end_date__gte=today),
            user=user, trackable__in=trackables, start_date__lte=today,
        ).values_list('trackable_id', flat=True)
    )
    return [t for t in trackables if t.pk not in paused_ids and is_scheduled(t.schedule, today)]


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
