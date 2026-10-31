"""Celery tasks of the tracking engine."""

from datetime import datetime, timedelta

from celery import shared_task
from django.utils import timezone

from . import selectors, services

BATCH_SIZE = 200


@shared_task(ignore_result=True)
def recompute_streak(trackable_id: str) -> None:
    """Rebuild the StreakCache of one trackable from CheckIn + Pause (idempotent)."""
    services.refresh_streak_cache(trackable_id)


@shared_task(ignore_result=True)
def recompute_streaks(trackable_ids: list[str]) -> None:
    """Rebuild several StreakCaches (one batch of the hourly fan-out)."""
    for trackable_id in trackable_ids:
        services.refresh_streak_cache(trackable_id)


@shared_task(ignore_result=True)
def recompute_streaks_after_local_midnight(now: str | None = None) -> int:
    """
    Hourly (Celery beat). For users whose local midnight passed during the last full
    hour, rebuild every streak: a day missed without any check-in or pause change
    must still reset `current`, and `computed_for_date` must move to the new day.

    The window is (previous full hour, this full hour], so a late beat run neither
    skips nor repeats a midnight. Half-hour zones are caught at the next full hour.
    Returns the number of trackables queued. `now` (ISO 8601) is for tests/replays.
    """
    instant = datetime.fromisoformat(now) if now else timezone.now()
    end = instant.replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=1)

    ids = [str(pk) for pk in selectors.trackable_ids_for_new_day(start, end)]
    for i in range(0, len(ids), BATCH_SIZE):
        recompute_streaks.delay(ids[i:i + BATCH_SIZE])
    return len(ids)
