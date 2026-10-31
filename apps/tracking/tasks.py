"""Celery tasks of the tracking engine."""

from celery import shared_task

from . import services


@shared_task(ignore_result=True)
def recompute_streak(trackable_id: str) -> None:
    """Rebuild the StreakCache of one trackable from CheckIn + Pause (idempotent)."""
    services.refresh_streak_cache(trackable_id)
