"""
Writes of the health module. Views (and later the AI confirmation flow) call these.
Trackables are created and updated through tracking.services only.
"""

from datetime import timedelta
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from apps.tracking import services as tracking
from apps.tracking.models import Trackable

from .calculations import WEIGHT_KG_RANGE, PlanInputs, Targets, calculate_targets
from .models import HealthMetric, HealthPlan, WeightEntry

__all__ = ['preview_plan', 'confirm_plan', 'log_weight']

MANAGED_BY = 'health'  # tracking.Trackable.managed_by of the metric trackables
WEIGHT_BACKFILL_DAYS = 30  # weight has no streak: a wider window than check-ins

Metric = HealthMetric.Metric
# metric -> how its trackable looks, and which plan field holds its target
METRIC_TRACKABLES = {
    Metric.WATER: {'name': 'Water', 'kind': Trackable.Kind.QUANTITY, 'unit': 'ml', 'icon': 'water',
                   'target': 'water_ml'},
    Metric.SLEEP: {'name': 'Sleep', 'kind': Trackable.Kind.QUANTITY, 'unit': 'h', 'icon': 'sleep',
                   'target': 'sleep_hours'},
    Metric.STEPS: {'name': 'Steps', 'kind': Trackable.Kind.QUANTITY, 'unit': 'steps', 'icon': 'steps',
                   'target': 'steps'},
    Metric.ACTIVITY: {'name': 'Activity', 'kind': Trackable.Kind.DURATION, 'unit': 'min', 'icon': 'activity',
                      'target': 'activity_minutes'},
}


# ----------------------------- plan ---------------------------------------- #
def preview_plan(inputs: PlanInputs) -> Targets:
    """Calculate the targets for these inputs. Saves nothing."""
    return calculate_targets(inputs)


@transaction.atomic
def confirm_plan(user, inputs: PlanInputs) -> HealthPlan:
    """
    Save the plan as a new version effective today (the user's local date). A second
    confirm on the same day updates today's version: earlier dates keep their plan.
    Also records today's weight and creates/updates the 4 metric trackables.
    """
    targets = calculate_targets(inputs)
    # Serialise confirms of the same user, so the metric trackables are never duplicated.
    get_user_model().objects.select_for_update().only('pk').get(pk=user.pk)
    today = tracking.user_today(user)

    plan, _ = HealthPlan.objects.update_or_create(
        user=user, effective_from=today,
        defaults={
            'goal': inputs.goal, 'sex': inputs.sex, 'birth_year': today.year - inputs.age,
            'height_cm': inputs.height_cm, 'activity_level': inputs.activity_level,
            'mode': inputs.mode, 'water_mode': inputs.water_mode,
            'calories': targets.calories, 'protein_g': targets.protein_g, 'carbs_g': targets.carbs_g,
            'fat_g': targets.fat_g, 'water_ml': targets.water_ml, 'steps': targets.steps,
            'activity_minutes': targets.activity_minutes, 'sleep_hours': targets.sleep_hours,
            'calorie_floor_applied': targets.calorie_floor_applied,
        },
    )
    log_weight(user, today, inputs.weight_kg)
    _sync_metric_trackables(user, plan)
    return plan


def _sync_metric_trackables(user, plan: HealthPlan) -> None:
    """Exactly one trackable per metric per user, found through HealthMetric (never by name)."""
    links = {link.metric: link for link in HealthMetric.objects.filter(user=user).select_related('trackable')}
    for metric, spec in METRIC_TRACKABLES.items():
        target = getattr(plan, spec['target'])
        link = links.get(metric)
        if link is None:
            trackable = tracking.create_trackable(
                user, name=spec['name'], domain=Trackable.Domain.HEALTH, kind=spec['kind'],
                unit=spec['unit'], icon=spec['icon'], target_value=target, managed_by=MANAGED_BY,
            )
            HealthMetric.objects.create(user=user, metric=metric, trackable=trackable)
        elif link.trackable.target_value != target:
            tracking.update_trackable(user, link.trackable, managed_by=MANAGED_BY, target_value=target)


# ----------------------------- weight -------------------------------------- #
def _weight(value) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValidationError({'weight_kg': 'Must be a number.'}) from None
    low, high = WEIGHT_KG_RANGE
    if not result.is_finite() or not (low <= result <= high):
        raise ValidationError({'weight_kg': f'Must be between {low} and {high} kg.'})
    return result.quantize(Decimal('0.01'))


@transaction.atomic
def log_weight(user, d, weight_kg) -> WeightEntry:
    """
    Record the weight on the user's local date `d` (one per day: a second log that
    day replaces it). Accepted for the last 30 days, never a future date.
    """
    today = tracking.user_today(user)
    if d > today:
        raise ValidationError({'date': 'Cannot record a future date.'})
    if d < today - timedelta(days=WEIGHT_BACKFILL_DAYS):
        raise ValidationError({'date': f'Weight can be recorded for the last {WEIGHT_BACKFILL_DAYS} days only.'})
    entry, _ = WeightEntry.objects.update_or_create(user=user, date=d, defaults={'weight_kg': _weight(weight_kg)})
    return entry
