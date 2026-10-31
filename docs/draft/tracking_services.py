"""
Core logic of the tracking engine. Views, agents and Celery tasks call these;
nothing else should re-implement "is it due?" or streak rules.
"""
from datetime import date, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from dateutil.rrule import rrulestr
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from .models import CheckIn, Pause, Schedule, StreakCache, Trackable


def user_today(user) -> date:
    """The user's local calendar date, the only 'today' that matters."""
    return timezone.now().astimezone(ZoneInfo(user.timezone)).date()


# ----------------------------- pauses -------------------------------------- #
def paused_dates(user, trackable, start: date, end: date) -> set[date]:
    """All dates in [start, end] covered by a global or trackable-specific pause."""
    pauses = Pause.objects.filter(
        Q(trackable__isnull=True) | Q(trackable=trackable),
        user=user,
        start_date__lte=end,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=start))

    result = set()
    for p in pauses:
        d = max(p.start_date, start)
        last = min(p.end_date or end, end)
        while d <= last:
            result.add(d)
            d += timedelta(days=1)
    return result


# ----------------------------- schedule ------------------------------------ #
def is_scheduled(schedule: Schedule, d: date) -> bool:
    """Is the trackable expected on date d (ignoring pauses)?"""
    t = schedule.trackable
    if d < t.start_date or (t.end_date and d > t.end_date):
        return False

    f = schedule.frequency
    if f == Schedule.Frequency.DAILY:
        return True
    if f == Schedule.Frequency.WEEKDAYS:
        return bool(schedule.weekdays_mask & (1 << d.weekday()))
    if f == Schedule.Frequency.EVERY_N_DAYS:
        return (d - t.start_date).days % (schedule.interval_days or 1) == 0
    if f == Schedule.Frequency.RRULE:
        rule = rrulestr(schedule.rrule, dtstart=t.start_date)
        return bool(rule.between(_dt(d), _dt(d), inc=True))
    # TIMES_PER_WEEK / TIMES_PER_MONTH: any day is valid; evaluated per period
    return True


def _dt(d: date):
    from datetime import datetime
    return datetime(d.year, d.month, d.day)


def due_today(user):
    """Trackables to show on the user's 'Today' screen."""
    today = user_today(user)
    result = []
    qs = Trackable.objects.filter(user=user, is_archived=False).select_related("schedule")
    for t in qs:
        if today in paused_dates(user, t, today, today):
            continue
        if is_scheduled(t.schedule, today):
            result.append(t)
    return result


# ----------------------------- check-ins ----------------------------------- #
@transaction.atomic
def log_checkin(user, trackable, d: date, value=None, source=CheckIn.Source.MANUAL, note=""):
    """
    Idempotent per (trackable, date). Quantity logs accumulate
    (2 glasses + 1 glass = 3). Used by the UI, voice agent and photo agent alike.
    """
    checkin, created = CheckIn.objects.select_for_update().get_or_create(
        trackable=trackable, date=d,
        defaults={"user": user, "value": value, "source": source, "note": note},
    )
    if not created and value is not None and trackable.kind != Trackable.Kind.BOOLEAN:
        CheckIn.objects.filter(pk=checkin.pk).update(value=F("value") + Decimal(value))
        checkin.refresh_from_db()

    checkin.status = _status_for(trackable, checkin.value)
    checkin.save(update_fields=["status", "updated_at"])
    transaction.on_commit(lambda: recompute_streak.delay(str(trackable.pk)))  # Celery
    return checkin


def _status_for(trackable, value):
    if trackable.kind == Trackable.Kind.BOOLEAN or trackable.target_value is None:
        return CheckIn.Status.DONE
    return CheckIn.Status.DONE if (value or 0) >= trackable.target_value else CheckIn.Status.PARTIAL


# ----------------------------- streaks ------------------------------------- #
def compute_streak(trackable, today: date, lookback_days=730):
    """
    Walk back from today. Paused days and non-scheduled days are skipped:
    they neither break nor extend the streak. Today not yet done doesn't break it.
    (TIMES_PER_WEEK habits need a per-week variant of this loop.)
    """
    user = trackable.user
    start = today - timedelta(days=lookback_days)
    done = set(
        CheckIn.objects.filter(
            trackable=trackable, date__gte=start, status=CheckIn.Status.DONE
        ).values_list("date", flat=True)
    )
    paused = paused_dates(user, trackable, start, today)

    current, d = 0, today
    while d >= max(start, trackable.start_date):
        if d in paused or not is_scheduled(trackable.schedule, d):
            d -= timedelta(days=1)
            continue
        if d in done:
            current += 1
        elif d != today:
            break
        d -= timedelta(days=1)
    return current


# Celery task (in tasks.py):
# @shared_task
# def recompute_streak(trackable_id):
#     t = Trackable.objects.select_related("schedule", "user").get(pk=trackable_id)
#     today = user_today(t.user)
#     current = compute_streak(t, today)
#     cache, _ = StreakCache.objects.get_or_create(trackable=t)
#     cache.current = current
#     cache.longest = max(cache.longest, current)
#     cache.computed_for_date = today
#     cache.save()
