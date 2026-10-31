"""
health: the user's plan and targets, body weight, and the link to the metric
trackables (water, sleep, steps, activity) it creates in tracking.

Write through health.services only. Dates are the user's local dates.
"""

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from apps.tracking.models import TimeStampedModel, Trackable

from .calculations import (
    HEIGHT_CM_RANGE,
    WEIGHT_KG_RANGE,
    ActivityLevel,
    Goal,
    Mode,
    Sex,
)


class HealthPlan(TimeStampedModel):
    """
    One version of the user's plan: the inputs and the targets computed from them.
    A new row each time the plan changes; a second change on the same day updates
    that day's row. The plan that applied on a date is the latest one with
    effective_from <= date, so changing the plan never changes past dates.
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='health_plans')
    effective_from = models.DateField()  # user's local date

    # Inputs (weight lives in WeightEntry)
    goal = models.CharField(max_length=10, choices=Goal.choices)
    sex = models.CharField(max_length=10, choices=Sex.choices)
    birth_year = models.PositiveSmallIntegerField()  # the UI sends an age
    height_cm = models.DecimalField(
        max_digits=4, decimal_places=1,
        validators=[MinValueValidator(HEIGHT_CM_RANGE[0]), MaxValueValidator(HEIGHT_CM_RANGE[1])],
    )
    activity_level = models.CharField(max_length=15, choices=ActivityLevel.choices)
    mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.AUTO)
    water_mode = models.CharField(max_length=10, choices=Mode.choices, default=Mode.AUTO)

    # Targets
    calories = models.PositiveIntegerField()
    protein_g = models.PositiveIntegerField()
    carbs_g = models.PositiveIntegerField()
    fat_g = models.PositiveIntegerField()
    water_ml = models.PositiveIntegerField()
    steps = models.PositiveIntegerField()
    activity_minutes = models.PositiveSmallIntegerField()
    sleep_hours = models.DecimalField(max_digits=3, decimal_places=1)
    calorie_floor_applied = models.BooleanField(
        default=False, help_text='The calories were raised to the safety floor (1500 men, 1200 women).',
    )

    class Meta:
        ordering = ['-effective_from']
        constraints = [
            models.UniqueConstraint(fields=['user', 'effective_from'], name='uniq_health_plan_per_day'),
        ]

    def __str__(self):
        return f'{self.user} plan from {self.effective_from}: {self.calories} kcal'


class WeightEntry(TimeStampedModel):
    """Body weight on one local date (one per day). BMI is computed, never stored."""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='weights')
    date = models.DateField()
    weight_kg = models.DecimalField(
        max_digits=5, decimal_places=2,
        validators=[MinValueValidator(WEIGHT_KG_RANGE[0]), MaxValueValidator(WEIGHT_KG_RANGE[1])],
    )

    class Meta:
        ordering = ['-date']
        constraints = [models.UniqueConstraint(fields=['user', 'date'], name='uniq_weight_per_day')]

    def __str__(self):
        return f'{self.user} on {self.date}: {self.weight_kg} kg'


class HealthMetric(TimeStampedModel):
    """
    The link between a health metric and its trackable in tracking. Health owns it:
    exactly one trackable per metric per user, found through this row (never by name).
    """

    class Metric(models.TextChoices):
        WATER = 'water', 'Water'
        SLEEP = 'sleep', 'Sleep'
        STEPS = 'steps', 'Steps'
        ACTIVITY = 'activity', 'Activity'

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='health_metrics')
    metric = models.CharField(max_length=10, choices=Metric.choices)
    trackable = models.OneToOneField(Trackable, on_delete=models.CASCADE, related_name='health_metric')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'metric'], name='uniq_health_metric_per_user')]

    def __str__(self):
        return f'{self.user}: {self.metric}'
