"""health_today: the dashboard data of one day."""

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.health import selectors, services
from apps.health.calculations import Mode
from apps.tracking import services as tracking

from .test_calculations import REFERENCE  # 2100 kcal, water 4000, sleep 8, steps 8000, activity 30
from .test_meals import egg, koshari  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db

TODAY = date(2026, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture
def clock(freeze_utc):
    def at(d: date):
        freeze_utc(d.year, d.month, d.day, 12, 0)
    at(TODAY)
    return at


@pytest.fixture
def planned(user, clock):
    """A plan confirmed two days ago, so yesterday and today both have it."""
    clock(TODAY - timedelta(days=2))
    services.confirm_plan(user, REFERENCE)
    clock(TODAY)
    return selectors.metric_trackables(user)


def checkin(user, metrics, name, value, d=TODAY):
    return tracking.log_checkin(user, metrics[name], d, value=value)


def full_day(user, metrics, koshari, d):
    """Exactly on target: 4 plates of koshari = 1400 g = 2100 kcal, and every metric at its target."""
    services.log_meal(user, date=d, meal_type='lunch', items=[{'food': koshari, 'portion': 'plate', 'quantity': 4}])
    for name, value in (('water', 4000), ('sleep', 8), ('steps', 8000), ('activity', 30)):
        checkin(user, metrics, name, value, d)


# ----------------------------- no plan ------------------------------------- #
def test_no_plan(user, clock, koshari):
    services.log_meal(user, date=TODAY, meal_type='lunch', items=[{'food': koshari, 'portion': 'plate'}])
    t = selectors.health_today(user)
    assert (t.plan, t.metrics, t.score) == (None, None, None)
    assert t.nutrition['kcal'] == selectors.NutrientLine(Decimal(525), None, None, None)  # eaten is still shown
    assert t.bmi.reason == 'no_weight'


# ----------------------------- a full day ---------------------------------- #
def test_full_day_on_target(user, planned, koshari):
    full_day(user, planned, koshari, YESTERDAY)
    t = selectors.health_today(user, YESTERDAY)
    assert t.nutrition['kcal'] == selectors.NutrientLine(Decimal(2100), Decimal(2100), Decimal(0), 100)
    assert t.nutrition['protein_g'].percent == 44  # 70 / 160 = 43.75
    assert {n: (m.value, m.target, m.unit, m.percent) for n, m in t.metrics.items()} == {
        'water': (4000, 4000, 'ml', 100), 'sleep': (8, 8, 'h', 100),
        'steps': (8000, 8000, 'steps', 100), 'activity': (30, 30, 'min', 100)}
    assert t.score.total == 100
    assert t.score.excluded == {}


def test_metric_ids_for_the_buttons(user, planned):
    c = checkin(user, planned, 'water', 500)
    water = selectors.health_today(user).metrics['water']
    assert (water.trackable_id, water.checkin_id, water.checkin_status) == (planned['water'].pk, c.pk, 'partial')
    steps = selectors.health_today(user).metrics['steps']
    assert (steps.trackable_id, steps.checkin_id, steps.value) == (planned['steps'].pk, None, 0)


def test_percent_is_not_capped_but_the_score_is(user, planned, koshari):
    full_day(user, planned, koshari, YESTERDAY)
    checkin(user, planned, 'water', 2000, YESTERDAY)  # 6000 ml
    t = selectors.health_today(user, YESTERDAY)
    assert (t.metrics['water'].percent, t.score.breakdown['water']) == (150, 100)


# ----------------------------- today: calories in progress ----------------- #
def test_today_morning(user, planned):
    checkin(user, planned, 'sleep', 8)
    t = selectors.health_today(user)
    assert t.score.excluded == {'calories': 'in_progress'}
    assert t.score.total == 25  # sleep 100, the rest 0


def test_today_over_target_counts(user, planned, koshari):
    services.log_meal(user, date=TODAY, meal_type='lunch',
                      items=[{'food': koshari, 'portion': 'plate', 'quantity': 6}])  # 3150 kcal = +50%
    t = selectors.health_today(user)
    assert t.score.breakdown['calories'] == 0
    assert 'calories' not in t.score.excluded


def test_same_values_on_a_past_date_count_calories(user, planned):
    checkin(user, planned, 'sleep', 8, YESTERDAY)
    t = selectors.health_today(user, YESTERDAY)
    assert t.score.breakdown['calories'] == 0  # nothing logged = 0
    assert t.score.total == 20                 # (0 + 0 + 100 + 0 + 0) / 5


# ----------------------------- plan of that date --------------------------- #
def test_a_past_date_uses_the_plan_that_applied_then(user, planned, koshari):
    full_day(user, planned, koshari, YESTERDAY)  # on target for the old plan
    services.confirm_plan(user, replace(REFERENCE, water_mode=Mode.MANUAL, water_ml=2000, weight_kg=Decimal(90)))

    past = selectors.health_today(user, YESTERDAY)
    assert (past.plan.water_ml, past.metrics['water'].target, past.metrics['water'].percent) == (4000, 4000, 100)
    assert past.nutrition['kcal'].target == 2100
    assert past.score.total == 100
    assert past.bmi.value == Decimal('25.2')  # yesterday's weight (80 kg), not today's 90

    now = selectors.health_today(user, TODAY)
    assert now.metrics['water'].target == 2000
    assert now.plan.calories != 2100 and now.bmi.value == Decimal('28.4')


# ----------------------------- pauses and skips ---------------------------- #
def test_paused_metric_is_excluded(user, planned, koshari):
    tracking.create_pause(user, start_date=YESTERDAY, trackable=planned['steps'])
    services.log_meal(user, date=YESTERDAY, meal_type='lunch',
                      items=[{'food': koshari, 'portion': 'plate', 'quantity': 4}])
    for name, value in (('water', 4000), ('sleep', 8), ('activity', 30)):
        checkin(user, planned, name, value, YESTERDAY)  # no steps: paused
    t = selectors.health_today(user, YESTERDAY)
    assert t.metrics['steps'].paused is True
    assert t.score.excluded == {'steps': 'paused'}
    assert t.score.breakdown == {'calories': 100, 'water': 100, 'sleep': 100, 'activity': 100}
    assert t.score.total == 100  # the paused metric doesn't pull the average down


def test_everything_paused_gives_no_score(user, planned):
    tracking.create_pause(user, start_date=TODAY)
    t = selectors.health_today(user)
    assert t.everything_paused is True
    assert t.score is None
    assert all(m.paused for m in t.metrics.values())


def test_a_skipped_metric_counts_as_zero(user, planned, koshari):
    full_day(user, planned, koshari, YESTERDAY)
    tracking.delete_checkin(user, tracking.log_checkin(user, planned['activity'], YESTERDAY, value=1))
    tracking.skip_checkin(user, planned['activity'], YESTERDAY)
    t = selectors.health_today(user, YESTERDAY)
    assert (t.metrics['activity'].value, t.metrics['activity'].checkin_status) == (0, 'skipped')
    assert t.score.breakdown['activity'] == 0
    assert t.score.total == 80


# ----------------------------- dates --------------------------------------- #
def test_editable_today_and_yesterday_only(user, planned):
    assert selectors.health_today(user, TODAY).editable is True
    assert selectors.health_today(user, YESTERDAY).editable is True
    assert selectors.health_today(user, TODAY - timedelta(days=2)).editable is False


def test_future_dates_are_refused(user, planned):
    with pytest.raises(ValidationError):
        selectors.health_today(user, TODAY + timedelta(days=1))


def test_date_is_the_users_local_date(make_user, freeze_utc):
    cairo = make_user(timezone='Africa/Cairo')
    freeze_utc(2026, 3, 10, 23, 30)  # March 11 in Cairo
    assert selectors.health_today(cairo).date == date(2026, 3, 11)


# ----------------------------- isolation ----------------------------------- #
def test_other_users_data_never_appears(user, planned, make_user, koshari):
    other = make_user()
    services.confirm_plan(other, replace(REFERENCE, weight_kg=Decimal(60)))
    full_day(other, selectors.metric_trackables(other), koshari, TODAY)
    tracking.create_pause(other, start_date=TODAY)

    t = selectors.health_today(user)
    assert t.nutrition['kcal'].eaten == 0
    assert all(m.value == 0 and m.checkin_id is None and not m.paused for m in t.metrics.values())
    assert t.everything_paused is False
    assert t.bmi.value == Decimal('25.2')
    assert {m.trackable_id for m in t.metrics.values()} == {tr.pk for tr in planned.values()}


# ----------------------------- query count --------------------------------- #
@pytest.mark.parametrize('meals', [0, 5])
def test_fixed_number_of_queries(user, planned, koshari, meals):
    for _ in range(meals):
        services.log_meal(user, date=TODAY, meal_type='snack', items=[{'food': koshari, 'grams': 10}] * 3)
    if meals:
        for name in ('water', 'sleep', 'steps', 'activity'):
            checkin(user, planned, name, 1)
        tracking.create_pause(user, start_date=TODAY, trackable=planned['steps'])
    with CaptureQueriesContext(connection) as ctx:
        selectors.health_today(user)
    assert len(ctx) == 6  # plan, meal totals, metric links, check-ins, pauses, weight


def test_fixed_number_of_queries_without_plan(user, clock):
    with CaptureQueriesContext(connection) as ctx:
        selectors.health_today(user)
    assert len(ctx) == 3  # plan, meal totals, weight
