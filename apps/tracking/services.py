"""
Writes of the tracking engine. Domain apps (health, spiritual, tasks), the AI
confirmation flow and the views all record through these functions.
"""

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce

from . import selectors
from .dates import user_today
from .models import CheckIn, Pause, Schedule, StreakCache, Trackable

__all__ = [
    'user_today', 'create_trackable', 'update_trackable', 'archive_trackable', 'unarchive_trackable',
    'log_checkin', 'skip_checkin', 'set_checkin_value', 'delete_checkin',
    'create_pause', 'end_pause', 'refresh_streak_cache',
]

TRACKABLE_FIELDS = {
    'name', 'domain', 'kind', 'icon', 'color', 'target_value', 'unit', 'start_date', 'end_date', 'config',
}
SCHEDULE_FIELDS = {'frequency', 'weekdays_mask', 'times_per_period', 'interval_days', 'rrule'}


def _queue_recompute(*trackable_ids) -> None:
    """Recompute streak caches in Celery once the transaction commits (never in the request)."""
    from .tasks import recompute_streak  # tasks imports services

    for trackable_id in trackable_ids:
        transaction.on_commit(lambda pk=str(trackable_id): recompute_streak.delay(pk))


def _owned_trackable(user, trackable, *, lock=False) -> Trackable:
    qs = Trackable.objects.select_related('schedule')
    if lock:
        qs = qs.select_for_update(of=('self',))
    try:
        return qs.get(pk=trackable.pk, user=user)
    except Trackable.DoesNotExist:
        raise ValidationError({'trackable': 'Unknown trackable.'}) from None


def _decimal(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({'value': 'Must be a number.'}) from None
    if not result.is_finite() or result <= 0:
        raise ValidationError({'value': 'Must be a positive number.'})
    return result.quantize(Decimal('0.01'))


# ----------------------------- trackables ---------------------------------- #
@transaction.atomic
def create_trackable(user, *, name: str, schedule: dict | None = None, **fields) -> Trackable:
    """
    Create a trackable and its schedule together (every trackable has one).
    `start_date` defaults to the user's today; `schedule` defaults to daily.
    """
    unknown = (set(fields) - TRACKABLE_FIELDS) | (set(schedule or {}) - SCHEDULE_FIELDS)
    if unknown:
        raise ValidationError(f'Unknown fields: {", ".join(sorted(unknown))}.')
    fields.setdefault('start_date', user_today(user))

    trackable = Trackable(user=user, name=name, **fields)
    trackable.full_clean()
    trackable.save()

    sched = Schedule(trackable=trackable, **(schedule or {}))
    sched.full_clean()
    sched.save()
    trackable.schedule = sched
    return trackable


# Changes that alter how history is read: the streak must be rebuilt.
_RECOMPUTE_FIELDS = {'start_date', 'end_date'}


@transaction.atomic
def update_trackable(user, trackable, *, schedule: dict | None = None, **fields) -> Trackable:
    """
    Edit a trackable and/or its schedule. `kind` cannot change (stored values would
    lose their meaning), and `start_date` cannot move past existing check-ins.
    A new target applies from now on: past check-ins keep the status they were given.
    """
    unknown = (set(fields) - TRACKABLE_FIELDS) | (set(schedule or {}) - SCHEDULE_FIELDS)
    if unknown:
        raise ValidationError(f'Unknown fields: {", ".join(sorted(unknown))}.')
    trackable = _owned_trackable(user, trackable, lock=True)

    if 'kind' in fields and fields['kind'] != trackable.kind:
        raise ValidationError({'kind': 'The kind of a trackable cannot change.'})
    if 'start_date' in fields:
        first = trackable.checkins.order_by('date').values_list('date', flat=True).first()
        if first is not None and fields['start_date'] > first:
            raise ValidationError({'start_date': f'There are check-ins from {first}; start on or before it.'})

    for name, value in fields.items():
        setattr(trackable, name, value)
    trackable.full_clean()
    trackable.save()

    if schedule:
        sched = trackable.schedule
        for name, value in schedule.items():
            setattr(sched, name, value)
        sched.full_clean()
        sched.save()

    if schedule or _RECOMPUTE_FIELDS & set(fields):
        _queue_recompute(trackable.pk)
    return trackable


@transaction.atomic
def archive_trackable(user, trackable) -> Trackable:
    """Hide a trackable from Today and stop new check-ins. History and streak stay."""
    trackable = _owned_trackable(user, trackable, lock=True)
    if not trackable.is_archived:
        trackable.is_archived = True
        trackable.save(update_fields=['is_archived', 'updated_at'])
    return trackable


@transaction.atomic
def unarchive_trackable(user, trackable) -> Trackable:
    trackable = _owned_trackable(user, trackable, lock=True)
    if trackable.is_archived:
        trackable.is_archived = False
        trackable.save(update_fields=['is_archived', 'updated_at'])
        _queue_recompute(trackable.pk)  # days missed while archived now count
    return trackable


# ----------------------------- check-ins ----------------------------------- #
def _status_for(trackable: Trackable, value) -> str:
    if trackable.kind == Trackable.Kind.BOOLEAN or trackable.target_value is None:
        return CheckIn.Status.DONE
    return CheckIn.Status.DONE if (value or 0) >= trackable.target_value else CheckIn.Status.PARTIAL


# Backfill window (CLAUDE.md rule 3): today and yesterday, in the user's local date.
BACKFILL_DAYS = 1


def _check_date(user, trackable, d: date) -> None:
    """Check-ins are accepted for today and yesterday only (every entry point, AI included)."""
    if trackable.is_archived:
        raise ValidationError({'trackable': 'This trackable is archived.'})
    today = user_today(user)
    if d > today:
        raise ValidationError({'date': 'Cannot record a future date.'})
    if d < today - timedelta(days=BACKFILL_DAYS):
        raise ValidationError({'date': 'Check-ins can only be recorded for today or yesterday.'})
    if d < trackable.start_date or (trackable.end_date and d > trackable.end_date):
        raise ValidationError({'date': 'Outside the trackable\'s active dates.'})


@transaction.atomic
def log_checkin(user, trackable, d: date, value=None, source=CheckIn.Source.MANUAL, note: str = '') -> CheckIn:
    """
    Record that `user` did `trackable` on their local date `d`.

    One row per (trackable, date): repeated logs accumulate (2 glasses + 1 glass = 3),
    whatever the source. The row keeps the source of the first log. Used by the UI,
    the voice/photo agents (after confirmation) and the domain apps alike.
    Logging a skipped day turns it into a real check-in.
    Only today and yesterday (user's local date) are accepted.
    """
    # Lock the trackable: concurrent logs for it serialise here, so the
    # get-then-create below cannot race into the unique constraint.
    trackable = _owned_trackable(user, trackable, lock=True)
    _check_date(user, trackable, d)
    if source not in CheckIn.Source.values:
        raise ValidationError({'source': f'Unknown source “{source}”.'})

    if trackable.kind == Trackable.Kind.BOOLEAN:
        amount = None  # done / not done: a value means nothing
    elif value is None:
        raise ValidationError({'value': f'Required for a {trackable.kind} trackable.'})
    else:
        amount = _decimal(value)

    checkin = CheckIn.objects.filter(trackable=trackable, date=d).first()
    if checkin is None:
        checkin = CheckIn.objects.create(
            user=user, trackable=trackable, date=d, value=amount, source=source, note=note,
            status=_status_for(trackable, amount),
        )
    else:
        updates = {}
        if amount is not None:
            updates['value'] = Coalesce(F('value'), Value(Decimal(0))) + amount
        if note:
            updates['note'] = Value(f'{checkin.note}\n{note}' if checkin.note else note)
        if updates:
            CheckIn.objects.filter(pk=checkin.pk).update(**updates)
            checkin.refresh_from_db()
        checkin.status = _status_for(trackable, checkin.value)
        checkin.save(update_fields=['status', 'updated_at'])

    _queue_recompute(trackable.pk)
    return checkin


@transaction.atomic
def skip_checkin(user, trackable, d: date, source=CheckIn.Source.MANUAL, note: str = '') -> CheckIn:
    """
    Consciously skip a day: it neither breaks nor extends the streak.
    A day that already has a check-in cannot be skipped.
    """
    trackable = _owned_trackable(user, trackable, lock=True)
    _check_date(user, trackable, d)
    checkin, created = CheckIn.objects.get_or_create(
        trackable=trackable, date=d,
        defaults={'user': user, 'status': CheckIn.Status.SKIPPED, 'source': source, 'note': note},
    )
    if not created and checkin.status != CheckIn.Status.SKIPPED:
        raise ValidationError({'date': 'This day already has a check-in.'})
    _queue_recompute(trackable.pk)
    return checkin


def _owned_checkin(user, checkin) -> tuple[CheckIn, Trackable]:
    """Lock the check-in's trackable (same lock as log_checkin), then load the check-in."""
    try:
        row = CheckIn.objects.only('trackable_id').get(pk=checkin.pk, user=user)
    except CheckIn.DoesNotExist:
        raise ValidationError({'checkin': 'Unknown check-in.'}) from None
    trackable = Trackable.objects.select_for_update(of=('self',)).select_related('schedule').get(
        pk=row.trackable_id, user=user,
    )
    return CheckIn.objects.get(pk=checkin.pk), trackable


@transaction.atomic
def set_checkin_value(user, checkin, value) -> CheckIn:
    """
    Fix a mistake: set the day's value directly (no accumulation) and recompute its status.
    Same window as logging: today or yesterday only. Boolean check-ins have no value:
    delete them instead.
    """
    checkin, trackable = _owned_checkin(user, checkin)
    _check_date(user, trackable, checkin.date)
    if trackable.kind == Trackable.Kind.BOOLEAN:
        raise ValidationError({'value': 'A done/not-done check-in has no value; delete it instead.'})
    checkin.value = _decimal(value)
    checkin.status = _status_for(trackable, checkin.value)
    checkin.save(update_fields=['value', 'status', 'updated_at'])
    _queue_recompute(trackable.pk)
    return checkin


@transaction.atomic
def delete_checkin(user, checkin) -> None:
    """Fix a mistake: remove a check-in (or a skip). Today or yesterday only."""
    checkin, trackable = _owned_checkin(user, checkin)
    _check_date(user, trackable, checkin.date)
    checkin.delete()
    _queue_recompute(trackable.pk)


# ----------------------------- pauses -------------------------------------- #
def _affected_trackable_ids(user, trackable):
    if trackable is not None:
        return [trackable.pk]
    return list(Trackable.objects.filter(user=user, is_archived=False).values_list('pk', flat=True))


@transaction.atomic
def create_pause(user, *, start_date: date, end_date: date | None = None, trackable=None,
                 reason=Pause.Reason.OTHER, note: str = '') -> Pause:
    """
    Pause one trackable, or everything (trackable=None). end_date=None: until resumed.
    Never touches check-ins: paused days are skipped when streaks are computed.
    start_date must be yesterday or later (no backdating over missed days);
    future pauses (a planned vacation) are allowed.
    """
    if start_date < user_today(user) - timedelta(days=BACKFILL_DAYS):
        raise ValidationError({'start_date': 'A pause can start yesterday at the earliest.'})
    if trackable is not None:
        trackable = _owned_trackable(user, trackable)
    pause = Pause(user=user, trackable=trackable, start_date=start_date, end_date=end_date,
                  reason=reason, note=note)
    pause.full_clean()  # includes the pause_end_after_start constraint
    pause.save()
    _queue_recompute(*_affected_trackable_ids(user, trackable))
    return pause


@transaction.atomic
def end_pause(user, pause: Pause, last_paused_day: date | None = None) -> Pause | None:
    """
    Resume. `last_paused_day` defaults to yesterday in the user's timezone, so today
    counts again; it cannot be earlier than yesterday (no rewriting of older days).
    A pause that would end before it starts (e.g. cancelling a planned vacation)
    is deleted; returns None in that case. A pause that is already over cannot be ended.
    """
    try:
        pause = Pause.objects.select_for_update().get(pk=pause.pk, user=user)
    except Pause.DoesNotExist:
        raise ValidationError({'pause': 'Unknown pause.'}) from None
    yesterday = user_today(user) - timedelta(days=BACKFILL_DAYS)
    if pause.end_date is not None and pause.end_date < yesterday:
        raise ValidationError({'pause': 'This pause is already over.'})
    last = last_paused_day or yesterday
    if last < yesterday:
        raise ValidationError({'last_paused_day': 'Must be yesterday or later.'})
    affected = _affected_trackable_ids(user, pause.trackable)
    if last < pause.start_date:
        pause.delete()
        pause = None
    else:
        pause.end_date = last
        pause.save(update_fields=['end_date', 'updated_at'])
    _queue_recompute(*affected)
    return pause


# ----------------------------- streak cache -------------------------------- #
@transaction.atomic
def refresh_streak_cache(trackable_id) -> StreakCache | None:
    """Rebuild a trackable's StreakCache from CheckIn + Pause. Called by tasks.recompute_streak."""
    trackable = (
        Trackable.objects.select_related('schedule', 'user').filter(pk=trackable_id).first()
    )
    if trackable is None:  # deleted before the task ran
        return None
    today = user_today(trackable.user)
    stats = selectors.compute_streak(trackable, today)
    cache, _ = StreakCache.objects.update_or_create(
        trackable=trackable,
        defaults={
            'current': stats.current,
            'longest': stats.longest,
            'completion_rate_30d': stats.completion_rate_30d,
            'computed_for_date': today,
        },
    )
    return cache
