"""Errors of the ai app that the API turns into specific responses (see api/views.py)."""


class AIDisabled(Exception):
    """The feature is switched off in the admin: 503 with code "ai_disabled"."""

    code = 'ai_disabled'


class DailyLimitExceeded(Exception):
    """Too many analyses today: 429 with the reset time (the user's next local midnight)."""

    code = 'daily_limit_exceeded'

    def __init__(self, limit: int, reset_at):
        super().__init__(f'At most {limit} analyses per day.')
        self.limit, self.reset_at = limit, reset_at
