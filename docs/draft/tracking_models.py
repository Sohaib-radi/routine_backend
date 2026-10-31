"""
tracking — the generic engine shared by every domain (tasks, health, spiritual, ...).

Rules:
- Every row is owned by a user; every query filters by user.
- A "date" is always the user's LOCAL date (user.timezone), never a UTC date.
- Future occurrences are never materialized: "what's due" is computed from Schedule.
- History is never rewritten: pauses change how history is interpreted, not the data.
"""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q


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
        GENERAL = "general"
        HEALTH = "health"
        SPIRITUAL = "spiritual"
        TASKS = "tasks"

    class Kind(models.TextChoices):
        BOOLEAN = "boolean"     # done / not done (pray Fajr)
        QUANTITY = "quantity"   # numeric target (8 glasses of water, 20 pages)
        DURATION = "duration"   # minutes (30 min walk)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="trackables"
    )
    domain = models.CharField(max_length=20, choices=Domain.choices, default=Domain.GENERAL)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.BOOLEAN)

    name = models.CharField(max_length=120)
    icon = models.CharField(max_length=50, blank=True)
    color = models.CharField(max_length=7, blank=True)

    target_value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    unit = models.CharField(max_length=20, blank=True)  # "glasses", "pages", "min"

    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    is_archived = models.BooleanField(default=False)

    # Domain-specific settings that don't deserve their own columns yet.
    config = models.JSONField(default=dict, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["user", "is_archived"]),
            models.Index(fields=["user", "domain"]),
        ]

    def __str__(self):
        return self.name


# --------------------------------------------------------------------------- #
# Schedule: when a trackable is due. Covers ~95% of cases with simple fields;
# `rrule` is the escape hatch for anything exotic.
# --------------------------------------------------------------------------- #
class Schedule(models.Model):
    class Frequency(models.TextChoices):
        DAILY = "daily"
        WEEKDAYS = "weekdays"           # specific days: Mon/Wed/Fri
        TIMES_PER_WEEK = "times_week"   # any 3 days per week
        TIMES_PER_MONTH = "times_month"
        EVERY_N_DAYS = "every_n_days"
        RRULE = "rrule"

    trackable = models.OneToOneField(Trackable, on_delete=models.CASCADE, related_name="schedule")
    frequency = models.CharField(max_length=20, choices=Frequency.choices, default=Frequency.DAILY)

    # Bitmask: Mon=1, Tue=2, Wed=4, Thu=8, Fri=16, Sat=32, Sun=64
    weekdays_mask = models.PositiveSmallIntegerField(default=127)
    times_per_period = models.PositiveSmallIntegerField(null=True, blank=True)
    interval_days = models.PositiveSmallIntegerField(null=True, blank=True)
    rrule = models.TextField(blank=True)  # RFC 5545, parsed with python-dateutil


class Reminder(TimeStampedModel):
    trackable = models.ForeignKey(Trackable, on_delete=models.CASCADE, related_name="reminders")
    time_of_day = models.TimeField()  # user's local time
    weekdays_mask = models.PositiveSmallIntegerField(default=127)
    is_active = models.BooleanField(default=True)


# --------------------------------------------------------------------------- #
# CheckIn: one row per trackable per local date. Repeated logs on the same day
# (another glass of water) increment `value` instead of adding rows.
# --------------------------------------------------------------------------- #
class CheckIn(TimeStampedModel):
    class Status(models.TextChoices):
        DONE = "done"
        PARTIAL = "partial"
        SKIPPED = "skipped"  # user consciously skipped: no streak penalty if allowed

    class Source(models.TextChoices):
        MANUAL = "manual"
        VOICE = "voice"
        PHOTO = "photo"
        AGENT = "agent"
        IMPORT = "import"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="checkins")
    trackable = models.ForeignKey(Trackable, on_delete=models.CASCADE, related_name="checkins")
    date = models.DateField()  # user's local date
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DONE)
    value = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    note = models.TextField(blank=True)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.MANUAL)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["trackable", "date"], name="uniq_checkin_per_day"),
        ]
        indexes = [
            models.Index(fields=["user", "date"]),       # "today" / calendar views
            models.Index(fields=["trackable", "date"]),  # streaks, history charts
        ]


# --------------------------------------------------------------------------- #
# Pause: vacation / sickness / travel. trackable=NULL pauses everything.
# end_date=NULL means "paused until I resume".
# --------------------------------------------------------------------------- #
class Pause(TimeStampedModel):
    class Reason(models.TextChoices):
        VACATION = "vacation"
        SICK = "sick"
        TRAVEL = "travel"
        OTHER = "other"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="pauses")
    trackable = models.ForeignKey(
        Trackable, on_delete=models.CASCADE, null=True, blank=True, related_name="pauses"
    )
    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=20, choices=Reason.choices, default=Reason.OTHER)
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")),
                name="pause_end_after_start",
            ),
        ]
        indexes = [models.Index(fields=["user", "start_date"])]


# --------------------------------------------------------------------------- #
# StreakCache: derived data, recomputed on check-in / pause changes
# (signal or Celery task). Never the source of truth: CheckIn is.
# --------------------------------------------------------------------------- #
class StreakCache(models.Model):
    trackable = models.OneToOneField(Trackable, on_delete=models.CASCADE, related_name="streak")
    current = models.PositiveIntegerField(default=0)
    longest = models.PositiveIntegerField(default=0)
    completion_rate_30d = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    computed_for_date = models.DateField(null=True, blank=True)
