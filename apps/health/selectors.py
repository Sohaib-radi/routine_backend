"""Read queries for health. Every query is scoped to the given user."""

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import Case, IntegerField, Prefetch, Q, Sum, Value, When
from django.db.models.functions import Coalesce

from apps.tracking.dates import user_today

from .models import Food, HealthMetric, HealthPlan, Meal, MealItem, WeightEntry
from .text import normalize


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


# BMI categories (WHO): upper bounds, exclusive.
BMI_CATEGORIES = (
    (Decimal('18.5'), 'underweight'),
    (Decimal(25), 'normal'),
    (Decimal(30), 'overweight'),
)
BMI_TOP_CATEGORY = 'obese'  # >= 30
NO_WEIGHT, NO_HEIGHT = 'no_weight', 'no_height'


def bmi_category(value: Decimal) -> str:
    for upper, name in BMI_CATEGORIES:
        if value < upper:
            return name
    return BMI_TOP_CATEGORY


@dataclass(frozen=True)
class Bmi:
    value: Decimal | None
    category: str | None
    reason: str | None  # why there is no value: NO_WEIGHT or NO_HEIGHT


def bmi_on(user, d: date, plan: HealthPlan | None) -> Bmi:
    """
    BMI on local date `d`: the latest weight on or before `d`, and the height of the
    plan that applied that day (pass plan_for(user, d)). One query.
    """
    weight = latest_weight(user, on=d)
    if weight is None:
        return Bmi(None, None, NO_WEIGHT)
    if plan is None:
        return Bmi(None, None, NO_HEIGHT)
    value = bmi(weight.weight_kg, plan.height_cm)
    return Bmi(value, bmi_category(value), None)


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


# ----------------------------- foods --------------------------------------- #
SEARCH_MIN_LENGTH = 2
SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 50
_SEPARATOR = r'(^| \| )'  # Food.search_text joins the normalized names with " | "


def foods_for(user):
    """Foods `user` can see: active global foods and their own custom foods."""
    return Food.objects.filter(Q(user__isnull=True) | Q(user=user), is_active=True)


def custom_foods_for(user):
    return Food.objects.filter(user=user)


def search_foods(user, query: str, limit: int = SEARCH_DEFAULT_LIMIT) -> list[Food]:
    """
    Search names and aliases in every language (global + the user's own foods).
    The query is normalized like the stored text, so "طعميه" finds "طعمية".
    Order: exact name, then names starting with the query, then trigram similarity.
    Uses the trigram index on search_text (LIKE '%q%').
    """
    q = normalize(query)
    if len(q) < SEARCH_MIN_LENGTH:
        return []
    limit = max(1, min(limit, SEARCH_MAX_LIMIT))
    escaped = re.escape(q)
    return list(
        foods_for(user).filter(search_text__contains=q)
        .annotate(
            rank=Case(
                When(search_text__regex=f'{_SEPARATOR}{escaped}( \\| |$)', then=Value(0)),
                When(search_text__regex=f'{_SEPARATOR}{escaped}', then=Value(1)),
                default=Value(2), output_field=IntegerField(),
            ),
            similarity=TrigramWordSimilarity(q, 'search_text'),
        )
        .order_by('rank', '-similarity', 'name_en', 'pk')
        .prefetch_related('portions')[:limit]
    )


# ----------------------------- meals & nutrition --------------------------- #
@dataclass(frozen=True)
class Nutrients:
    kcal: Decimal
    protein_g: Decimal
    carbs_g: Decimal
    fat_g: Decimal

    def __sub__(self, other: 'Nutrients') -> 'Nutrients':
        return Nutrients(*(getattr(self, f) - getattr(other, f) for f in NUTRIENT_FIELDS))


NUTRIENT_FIELDS = ('kcal', 'protein_g', 'carbs_g', 'fat_g')
ZERO = Nutrients(Decimal(0), Decimal(0), Decimal(0), Decimal(0))


def meals_for(user, d: date):
    """The user's meals on local date `d`, with their items. Two queries whatever the number of meals."""
    return (Meal.objects.filter(user=user, date=d).order_by('created_at')
            .prefetch_related(Prefetch('items', queryset=MealItem.objects.order_by('created_at'))))


def meal_totals(meal: Meal) -> Nutrients:
    """Totals of one meal from its items' snapshots (uses the prefetched items)."""
    items = list(meal.items.all())
    return Nutrients(*(sum((getattr(i, f) for i in items), Decimal(0)) for f in NUTRIENT_FIELDS))


@dataclass(frozen=True)
class DailyNutrition:
    date: date
    totals: Nutrients
    targets: Nutrients | None    # from the plan that applied on that date; None before the first plan
    remaining: Nutrients | None  # targets - totals (negative when over)


def daily_nutrition(user, d: date) -> DailyNutrition:
    """
    What the calories ring and macro cards show: totals from the meal snapshots,
    the targets of the plan that applied on `d`, and what remains. Two queries.
    """
    sums = MealItem.objects.filter(meal__user=user, meal__date=d).aggregate(
        **{f: Coalesce(Sum(f), Decimal(0)) for f in NUTRIENT_FIELDS}
    )
    totals = Nutrients(**sums)
    plan = plan_for(user, d)
    if plan is None:
        return DailyNutrition(d, totals, None, None)
    targets = Nutrients(Decimal(plan.calories), Decimal(plan.protein_g), Decimal(plan.carbs_g), Decimal(plan.fat_g))
    return DailyNutrition(d, totals, targets, targets - totals)
