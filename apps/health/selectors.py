"""Read queries for health. Every query is scoped to the given user."""

import re
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import Case, IntegerField, Q, Value, When

from apps.tracking.dates import user_today

from .models import Food, HealthMetric, HealthPlan, WeightEntry
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
