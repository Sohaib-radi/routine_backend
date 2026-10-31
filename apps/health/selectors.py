"""Read queries for health. Every query is scoped to the given user."""

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from apps.tracking.dates import user_today

from .models import HealthMetric, HealthPlan, WeightEntry


# ----------------------------- plans --------------------------------------- #
def plan_for(user, d: date) -> HealthPlan | None:
    """The plan that applied on the user's local date `d` (None before the first plan)."""
    return HealthPlan.objects.filter(user=user, effective_from__lte=d).order_by('-effective_from').first()


def current_plan(user) -> HealthPlan | None:
    return plan_for(user, user_today(user))


def plan_history(user):
    """Every version of the user's plan, newest first."""
    return HealthPlan.objects.filter(user=user).order_by('-effective_from')


def age_on(birth_year: int, d: date) -> int:
    """Age as stored (from the birth year only: may be one year high before the birthday)."""
    return d.year - birth_year


# ----------------------------- weight -------------------------------------- #
def weights_for(user):
    return WeightEntry.objects.filter(user=user).order_by('-date')


def latest_weight(user, on: date | None = None) -> WeightEntry | None:
    """The most recent weight on or before `on` (default: the user's today)."""
    on = on or user_today(user)
    return weights_for(user).filter(date__lte=on).first()


def bmi(weight_kg: Decimal, height_cm: Decimal) -> Decimal:
    """Body mass index, kg / m², one decimal. Computed, never stored."""
    meters = Decimal(height_cm) / 100
    return (Decimal(weight_kg) / (meters * meters)).quantize(Decimal('0.1'), rounding=ROUND_HALF_UP)


def current_bmi(user, plan: HealthPlan | None = None) -> Decimal | None:
    """BMI from the latest weight and the current plan's height (None if either is missing)."""
    plan = plan or current_plan(user)
    weight = latest_weight(user)
    if plan is None or weight is None:
        return None
    return bmi(weight.weight_kg, plan.height_cm)


# ----------------------------- metrics ------------------------------------- #
def metric_trackables(user) -> dict[str, object]:
    """{metric: trackable} for the metric trackables health created for `user`."""
    links = HealthMetric.objects.filter(user=user).select_related('trackable')
    return {link.metric: link.trackable for link in links}
