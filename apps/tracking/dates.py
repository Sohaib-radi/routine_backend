"""The user's local calendar. Every tracking date is a local date (CLAUDE.md rule 2)."""

from datetime import date
from zoneinfo import ZoneInfo

from django.utils import timezone


def user_today(user) -> date:
    """The user's local calendar date: the only 'today' that matters. Never date.today()."""
    return timezone.now().astimezone(ZoneInfo(user.timezone)).date()
