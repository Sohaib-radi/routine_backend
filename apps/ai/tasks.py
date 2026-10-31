"""Celery tasks of the ai app. Provider calls never run in the request."""

from celery import shared_task

from . import services


@shared_task(ignore_result=True, soft_time_limit=60, time_limit=90)
def analyze_meal_draft(draft_id: str) -> None:
    """Analyze one pending draft. Ends in ready or failed, never stuck in processing."""
    services.analyze_draft(draft_id)


@shared_task(ignore_result=True)
def sweep_meal_drafts() -> dict:
    """Every 5 minutes (beat): fail drafts stuck in pending/processing, expire old open drafts."""
    return services.sweep_drafts()
