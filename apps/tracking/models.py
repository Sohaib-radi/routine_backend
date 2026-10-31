"""
tracking: the generic engine shared by every domain (tasks, health, spiritual, ...).

Rules (CLAUDE.md):
- Every row is owned by a user; every query filters by user.
- A "date" is always the user's LOCAL date (user.timezone), never a UTC date.
- Future occurrences are never materialized: "what's due" is computed from Schedule.
- History is never rewritten: pauses change how history is interpreted, not the data.
- Write through tracking.services only.
"""

import uuid

from dateutil.rrule import rrulestr
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q

hex_color_validator = RegexValidator(regex=r'^#[0-9A-Fa-f]{6}$', message='Color must be a hex value like #728668.')

ALL_WEEKDAYS = 127  # Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64


class TimeStampedModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


# --------------------------------------------------------------------------- #
# Trackable: anything a user can do and record (habit, routine step, prayer,
# water intake, workout, Quran reading...). Domain apps attach their own data
# via OneToOne/FK to Trackable, or via `config`.
# --------------------------------------------------------------------------- #
class Trackable(TimeStampedModel):
    class Domain(models.TextChoices):
        GENERAL = 'general'
        HEALTH = 'health'
        SPIRITUAL = 'spiritual'
        TASKS = 'tasks'

    class Kind(models.TextChoices):
        BOOLEAN = 'boolean'     # done / not done (pray Fajr)
        QUANTITY = 'quantity'   # numeric target (8 glasses of water, 20 pages)
        DURATION = 'duration'   # minutes (30 min walk)

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='trackables')
    domain = models.CharField(max_length=20, choices=Domain.choices, default=Domain.GENERAL)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.BOOLEAN)

    name = models.CharField(max_length=120)
    icon = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=7, blank=True, validators=[hex_color_validator])

    target_value = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True, validators=[MinValueValidator(0)]
    )
    unit = models.CharField(max_length=20, blank=True)  # "glasses", "pages", "min"

    start_date = models.DateField()  # user's local date
    end_date = models.DateField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)

    # Domain-specific settings that don't deserve their own columns yet.
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'is_archived']),
            models.Index(fields=['user', 'domain']),
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F('start_date')),
                name='trackable_end_after_start',
            ),
        ]

    def __str__(self):
        return self.name


# --------------------------------------------------------------------------- #
# Schedule: when a trackable is due. Covers ~95% of cases with simple fields;
# `rrule` is the escape hatch for anything exotic.
# --------------------------------------------------------------------------- #
class Schedule(TimeStampedModel):
    class Frequency(models.TextChoices):
        DAILY = 'daily'
        WEEKDAYS = 'weekdays'           # specific days: Mon/Wed/Fri
        TIMES_PER_WEEK = 'times_week'   # any 3 days per week
        TIMES_PER_MONTH = 'times_month'
        EVERY_N_DAYS = 'every_n_days'
        RRULE = 'rrule'

    trackable = models.OneToOneField(Trackable, on_delete=models.CASCADE, related_name='schedule')
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.DAILY)

    # Bitmask: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64
    weekdays_mask = models.PositiveSmallIntegerField(
        default=ALL_WEEKDAYS, validators=[MinValueValidator(1), MaxValueValidator(ALL_WEEKDAYS)]
    )
    times_per_period = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    interval_days = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1)])
    rrule = models.TextField(blank=True)  # RFC 5545, parsed with python-dateutil

    def clean(self):
        f = self.Frequency
        if self.frequency in (f.TIMES_PER_WEEK, f.TIMES_PER_MONTH) and not self.times_per_period:
            raise ValidationError({'times_per_period': 'Required for this frequency.'})
        if self.frequency == f.EVERY_N_DAYS and not self.interval_days:
            raise ValidationError({'interval_days': 'Required for this frequency.'})
        if self.frequency == f.RRULE:
            if not self.rrule:
                raise ValidationError({'rrule': 'Required for this frequency.'})
            try:
                rrulestr(self.rrule)
            except (ValueError, TypeError) as exc:
                raise ValidationError({'rrule': f'Invalid RRULE: {exc}'}) from None

    def __str__(self):
        return f'{self.trackable} ({self.frequency})'


class Reminder(TimeStampedModel):
    trackable = models.ForeignKey(Trackable, on_delete=models.CASCADE, related_name='reminders')
    time_of_day = models.TimeField()  # user's local time
    weekdays_mask = models.PositiveSmallIntegerField(
        default=ALL_WEEKDAYS, validators=[MinValueValidator(1), MaxValueValidator(ALL_WEEKDAYS)]
    )
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f'{self.trackable} at {self.time_of_day:%H:%M}'


# --------------------------------------------------------------------------- #
# CheckIn: one row per trackable per local date. Repeated logs on the same day
# (another glass of water) increment `value` instead of adding rows.
# --------------------------------------------------------------------------- #
class CheckIn(TimeStampedModel):
    class Status(models.TextChoices):
        DONE = 'done'
        PARTIAL = 'partial'
        SKIPPED = 'skipped'  # consciously skipped: neither breaks nor extends a streak

    class Source(models.TextChoices):
        MANUAL = 'manual'
        VOICE = 'voice'
        PHOTO = 'photo'
        AGENT = 'agent'
        IMPORT = 'import'

    # Denormalized from trackable.user for per-user queries; services keep them equal.
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='checkins')
    trackable = models.ForeignKey(Trackable, on_delete=models.CASCADE, related_name='checkins')
    date = models.DateField()  # user's local date
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DONE)
    value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    note = models.TextField(blank=True)
    # Source of the first log of the day; later logs accumulate into this row.
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['trackable', 'date'], name='uniq_checkin_per_day'),
        ]
        indexes = [
            models.Index(fields=['user', 'date']),       # "today" / calendar views
            # (trackable, date) is covered by the unique constraint's index: streaks, charts
        ]

    def __str__(self):
        return f'{self.trackable} on {self.date}: {self.status}'


# --------------------------------------------------------------------------- #
# Pause: vacation / sickness / travel. trackable=NULL pauses everything.
# end_date=NULL means "paused until I resume". Both dates inclusive.
# --------------------------------------------------------------------------- #
class Pause(TimeStampedModel):
    class Reason(models.TextChoices):
        VACATION = 'vacation'
        SICK = 'sick'
        TRAVEL = 'travel'
        OTHER = 'other'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='pauses')
    trackable = models.ForeignKey(
        Trackable, on_delete=models.CASCADE, null=True, blank=True, related_name='pauses'
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.OTHER)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F('start_date')),
                name='pause_end_after_start',
            ),
        ]
        indexes = [models.Index(fields=['user', 'start_date'])]

    def __str__(self):
        scope = self.trackable or 'everything'
        return f'Pause of {scope} from {self.start_date} to {self.end_date or "open"}'


# --------------------------------------------------------------------------- #
# StreakCache: derived data, rebuilt from CheckIn + Pause by the Celery task
# tracking.tasks.recompute_streak. Never the source of truth (rule 5).
# --------------------------------------------------------------------------- #
class StreakCache(TimeStampedModel):
    trackable = models.OneToOneField(Trackable, on_delete=models.CASCADE, related_name='streak')
    current = models.PositiveIntegerField(default=0)
    longest = models.PositiveIntegerField(default=0)
    completion_rate_30d = models.DecimalField(max_digits=5, decimal_places=2, default=0)  # percent, 0-100
    computed_for_date = models.DateField(null=True, blank=True)

    def __str__(self):
        return f'{self.trackable}: {self.current} (longest {self.longest})'
