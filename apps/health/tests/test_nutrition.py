"""meals_for and daily_nutrition: totals from snapshots vs the plan for that date."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.health import selectors, services
from apps.health.calculations import Goal
from apps.health.models import Food

from .test_calculations import REFERENCE
from .test_meals import egg, koshari  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db

TODAY = date(2026, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture(autouse=True)
def today(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)


def log(user, meal_type, *items, d=TODAY):
    return services.log_meal(user, date=d, meal_type=meal_type, items=list(items))


def test_no_meals_no_plan(user):
    n = selectors.daily_nutrition(user, TODAY)
    assert n.totals == selectors.ZERO
    assert (n.targets, n.remaining) == (None, None)


def test_totals_add_up_every_meal_of_the_day(user, koshari, egg):
    log(user, 'breakfast', {'food': egg, 'portion': 'piece', 'quantity': 2})   # 100 g: 155 / 13 / 1.1 / 11
    log(user, 'lunch', {'food': koshari, 'portion': 'plate'})                  # 525 / 17.5 / 94.5 / 8.8
    log(user, 'snack', {'food': koshari, 'grams': 100})                         # 150 / 5 / 27 / 2.5
    log(user, 'dinner', {'food': koshari, 'grams': 100}, d=YESTERDAY)          # another day
    t = selectors.daily_nutrition(user, TODAY).totals
    assert t == selectors.Nutrients(Decimal('830.0'), Decimal('35.5'), Decimal('122.6'), Decimal('22.3'))


def test_remaining_against_the_plan(user, koshari):
    services.confirm_plan(user, REFERENCE)  # 2100 kcal, P 160, C 235, F 58
    log(user, 'lunch', {'food': koshari, 'portion': 'plate'})
    n = selectors.daily_nutrition(user, TODAY)
    assert n.targets == selectors.Nutrients(Decimal(2100), Decimal(160), Decimal(235), Decimal(58))
    assert n.remaining == selectors.Nutrients(Decimal('1575.0'), Decimal('142.5'), Decimal('140.5'),
                                              Decimal('49.2'))


def test_remaining_goes_negative_when_over(user, koshari):
    services.confirm_plan(user, REFERENCE)
    for _ in range(5):
        log(user, 'snack', {'food': koshari, 'portion': 'plate'})  # 5 x 525 = 2625
    assert selectors.daily_nutrition(user, TODAY).remaining.kcal == Decimal(-525)


def test_targets_come_from_the_plan_of_that_date(user, koshari, freeze_utc):
    freeze_utc(2026, 3, 9, 12, 0)
    services.confirm_plan(user, REFERENCE)                        # cut: 2100
    log(user, 'lunch', {'food': koshari, 'portion': 'plate'}, d=YESTERDAY)
    freeze_utc(2026, 3, 10, 12, 0)
    services.confirm_plan(user, replace(REFERENCE, goal=Goal.BULK))  # changed after the meal: 2720

    yesterday = selectors.daily_nutrition(user, YESTERDAY)
    assert (yesterday.targets.kcal, yesterday.remaining.kcal) == (2100, Decimal(1575))
    assert selectors.daily_nutrition(user, TODAY).targets.kcal == 2720


def test_editing_a_food_does_not_change_totals(user, koshari):
    log(user, 'lunch', {'food': koshari, 'portion': 'plate'})
    before = selectors.daily_nutrition(user, TODAY).totals
    Food.objects.filter(pk=koshari.pk).update(kcal=999, protein_g=90, carbs_g=5, fat_g=5)
    koshari.refresh_from_db()
    assert selectors.daily_nutrition(user, TODAY).totals == before
    koshari.delete()
    assert selectors.daily_nutrition(user, TODAY).totals == before


def test_totals_are_per_user(user, make_user, koshari):
    log(user, 'lunch', {'food': koshari, 'portion': 'plate'})
    assert selectors.daily_nutrition(make_user(), TODAY).totals == selectors.ZERO


def test_meals_for_and_meal_totals(user, koshari, egg):
    log(user, 'breakfast', {'food': egg, 'portion': 'piece'}, {'food': koshari, 'grams': 100})
    log(user, 'lunch', {'food': koshari, 'portion': 'plate'})
    meals = list(selectors.meals_for(user, TODAY))
    assert [m.meal_type for m in meals] == ['breakfast', 'lunch']
    assert selectors.meal_totals(meals[0]).kcal == Decimal('227.5')  # 77.5 + 150


@pytest.mark.parametrize('meal_count', [1, 6])
def test_fixed_number_of_queries(user, koshari, meal_count):
    for _ in range(meal_count):
        log(user, 'snack', *[{'food': koshari, 'grams': 10}] * 3)
    services.confirm_plan(user, REFERENCE)
    with CaptureQueriesContext(connection) as ctx:
        meals = list(selectors.meals_for(user, TODAY))
        [selectors.meal_totals(m) for m in meals]
    assert len(ctx) == 2
    with CaptureQueriesContext(connection) as ctx:
        selectors.daily_nutrition(user, TODAY)
    assert len(ctx) == 2
