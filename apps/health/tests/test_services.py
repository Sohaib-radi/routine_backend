import threading
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.core.exceptions import ValidationError
from django.db import connection

from apps.health import selectors, services
from apps.health.calculations import Goal, Mode
from apps.health.models import HealthMetric, HealthPlan, WeightEntry
from apps.tracking.models import Trackable

from .test_calculations import REFERENCE

pytestmark = pytest.mark.django_db

TODAY = date(2026, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)


def as_of(d: date):
    """Freeze the clock at noon UTC on `d`, so `d` is "today" for a UTC user."""
    return patch('django.utils.timezone.now', return_value=datetime(d.year, d.month, d.day, 12, tzinfo=dt_timezone.utc))


@pytest.fixture(autouse=True)
def today(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)


# ----------------------------- preview ------------------------------------- #
def test_preview_returns_targets_and_saves_nothing(user):
    targets = services.preview_plan(REFERENCE)
    assert targets.calories == 2100
    assert not HealthPlan.objects.exists()
    assert not WeightEntry.objects.exists()
    assert not Trackable.objects.exists()


# ----------------------------- confirm ------------------------------------- #
def test_confirm_saves_inputs_and_targets(user):
    plan = services.confirm_plan(user, REFERENCE)
    assert plan.effective_from == TODAY
    assert plan.birth_year == 2026 - 24
    assert (plan.goal, plan.sex, plan.height_cm, plan.activity_level, plan.mode) == (
        'cut', 'male', Decimal(178), 'light', 'auto')
    assert (plan.calories, plan.protein_g, plan.carbs_g, plan.fat_g, plan.water_ml) == (2100, 160, 235, 58, 4000)
    assert (plan.steps, plan.activity_minutes, plan.sleep_hours) == (8000, 30, Decimal(8))
    assert plan.calorie_floor_applied is False


def test_confirm_records_todays_weight(user):
    services.confirm_plan(user, REFERENCE)
    entry = WeightEntry.objects.get(user=user)
    assert (entry.date, entry.weight_kg) == (TODAY, Decimal(80))


def test_invalid_inputs_save_nothing(user):
    with pytest.raises(ValidationError):
        services.confirm_plan(user, replace(REFERENCE, age=17))
    assert not HealthPlan.objects.exists()
    assert not Trackable.objects.exists()


def test_second_confirm_same_day_updates_todays_row(user):
    services.confirm_plan(user, REFERENCE)
    plan = services.confirm_plan(user, replace(REFERENCE, goal=Goal.MAINTAIN))
    assert HealthPlan.objects.filter(user=user).count() == 1
    assert plan.calories == 2470


# ----------------------------- history ------------------------------------- #
def test_changing_the_plan_today_keeps_yesterdays_plan(user):
    with as_of(YESTERDAY):
        services.confirm_plan(user, REFERENCE)
    services.confirm_plan(user, replace(REFERENCE, goal=Goal.BULK))

    assert selectors.plan_for(user, YESTERDAY).calories == 2100
    assert selectors.plan_for(user, TODAY).calories == 2720
    assert selectors.current_plan(user).goal == 'bulk'
    assert [p.effective_from for p in selectors.plan_history(user)] == [TODAY, YESTERDAY]


def test_plan_for_uses_the_latest_version_on_or_before_the_date(user):
    with as_of(date(2026, 3, 1)):
        services.confirm_plan(user, REFERENCE)
    with as_of(date(2026, 3, 5)):
        services.confirm_plan(user, replace(REFERENCE, goal=Goal.MAINTAIN))
    assert selectors.plan_for(user, date(2026, 2, 28)) is None
    assert selectors.plan_for(user, date(2026, 3, 4)).goal == 'cut'
    assert selectors.plan_for(user, date(2026, 3, 5)).goal == 'maintain'
    assert selectors.plan_for(user, date(2026, 3, 9)).goal == 'maintain'


def test_plans_are_per_user(user, make_user):
    services.confirm_plan(user, REFERENCE)
    assert selectors.current_plan(make_user()) is None


def test_today_is_the_users_local_date(make_user, freeze_utc):
    """23:30 UTC on March 10 is already March 11 in Cairo (UTC+2)."""
    cairo = make_user(timezone='Africa/Cairo')
    freeze_utc(2026, 3, 10, 23, 30)
    assert services.confirm_plan(cairo, REFERENCE).effective_from == date(2026, 3, 11)


# ----------------------------- metric trackables --------------------------- #
def test_confirm_creates_the_four_metric_trackables(user):
    services.confirm_plan(user, REFERENCE)
    t = selectors.metric_trackables(user)
    assert set(t) == {'water', 'sleep', 'steps', 'activity'}
    assert (t['water'].kind, t['water'].unit, t['water'].target_value) == ('quantity', 'ml', 4000)
    assert (t['sleep'].kind, t['sleep'].unit, t['sleep'].target_value) == ('quantity', 'h', 8)
    assert (t['steps'].kind, t['steps'].target_value) == ('quantity', 8000)
    assert (t['activity'].kind, t['activity'].unit, t['activity'].target_value) == ('duration', 'min', 30)
    for trackable in t.values():
        assert (trackable.domain, trackable.managed_by, trackable.start_date) == ('health', 'health', TODAY)
        assert trackable.schedule.frequency == 'daily'


def test_saving_the_plan_twice_does_not_duplicate_trackables(user):
    services.confirm_plan(user, REFERENCE)
    with as_of(TODAY + timedelta(days=1)):
        services.confirm_plan(user, replace(REFERENCE, weight_kg=Decimal(90)))
    services.confirm_plan(user, REFERENCE)
    assert Trackable.objects.filter(user=user).count() == 4
    assert HealthMetric.objects.filter(user=user).count() == 4


def test_trackable_targets_follow_the_plan(user):
    services.confirm_plan(user, REFERENCE)
    services.confirm_plan(user, replace(REFERENCE, water_mode=Mode.MANUAL, water_ml=2500))
    assert selectors.metric_trackables(user)['water'].target_value == 2500


def test_trackables_are_found_by_link_not_name(user):
    services.confirm_plan(user, REFERENCE)
    water = selectors.metric_trackables(user)['water']
    Trackable.objects.filter(pk=water.pk).update(name='Hydration')  # renamed by the user
    services.confirm_plan(user, replace(REFERENCE, water_mode=Mode.MANUAL, water_ml=3000))
    water.refresh_from_db()
    assert (water.name, water.target_value) == ('Hydration', 3000)
    assert Trackable.objects.filter(user=user).count() == 4


def test_user_trackable_named_water_is_left_alone(user):
    from apps.tracking import services as tracking

    own = tracking.create_trackable(user, name='Water', kind='quantity', target_value=8, unit='glasses')
    services.confirm_plan(user, REFERENCE)
    own.refresh_from_db()
    assert (own.target_value, own.managed_by) == (8, '')
    assert Trackable.objects.filter(user=user).count() == 5


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
def test_concurrent_confirms_create_one_set_of_trackables(user):
    barrier = threading.Barrier(2)
    errors = []

    def confirm():
        try:
            barrier.wait()
            services.confirm_plan(user, REFERENCE)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=confirm) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert Trackable.objects.filter(user=user).count() == 4
    assert HealthPlan.objects.filter(user=user).count() == 1


# ----------------------------- weight -------------------------------------- #
def test_log_weight_one_per_day(user):
    services.log_weight(user, TODAY, 80)
    services.log_weight(user, TODAY, Decimal('79.4'))
    assert WeightEntry.objects.get(user=user).weight_kg == Decimal('79.40')


def test_weight_window(user):
    services.log_weight(user, TODAY - timedelta(days=30), 80)
    with pytest.raises(ValidationError) as exc:
        services.log_weight(user, TODAY - timedelta(days=31), 80)
    assert 'date' in exc.value.message_dict
    with pytest.raises(ValidationError) as exc:
        services.log_weight(user, TODAY + timedelta(days=1), 80)
    assert 'date' in exc.value.message_dict


@pytest.mark.parametrize('value', ['29.99', '300.01', 'abc', 'NaN'])
def test_weight_range(user, value):
    with pytest.raises(ValidationError) as exc:
        services.log_weight(user, TODAY, value)
    assert 'weight_kg' in exc.value.message_dict


def test_latest_weight_and_bmi(user):
    assert selectors.current_bmi(user) is None
    services.confirm_plan(user, REFERENCE)
    assert selectors.current_bmi(user) == Decimal('25.2')  # 80 / 1.78² = 25.249
    services.log_weight(user, TODAY, 75)  # the latest weight wins
    assert selectors.current_bmi(user) == Decimal('23.7')  # 75 / 3.1684 = 23.67


# ----------------------------- BMI categories ------------------------------ #
@pytest.mark.parametrize(('value', 'category'), [
    (Decimal('15.0'), 'underweight'),
    (Decimal('18.4'), 'underweight'),
    (Decimal('18.5'), 'normal'),
    (Decimal('24.9'), 'normal'),
    (Decimal('25.0'), 'overweight'),
    (Decimal('29.9'), 'overweight'),
    (Decimal('30.0'), 'obese'),
    (Decimal('41.2'), 'obese'),
])
def test_bmi_categories(value, category):
    assert selectors.bmi_category(value) == category


def test_bmi_on_a_date(user):
    services.confirm_plan(user, REFERENCE)  # 80 kg, 178 cm
    b = selectors.bmi_on(user, TODAY, selectors.plan_for(user, TODAY))
    assert (b.value, b.category, b.reason) == (Decimal('25.2'), 'overweight', None)


def test_bmi_uses_the_latest_weight_on_or_before_the_date(user):
    services.confirm_plan(user, REFERENCE)
    services.log_weight(user, TODAY - timedelta(days=5), 70)
    plan = selectors.plan_for(user, TODAY)
    assert selectors.bmi_on(user, TODAY - timedelta(days=3), plan).value == Decimal('22.1')  # 70 / 3.1684
    assert selectors.bmi_on(user, TODAY, plan).value == Decimal('25.2')


def test_bmi_without_weight(user):
    b = selectors.bmi_on(user, TODAY, None)
    assert (b.value, b.category, b.reason) == (None, None, 'no_weight')


def test_bmi_without_height(user):
    services.log_weight(user, TODAY, 80)
    b = selectors.bmi_on(user, TODAY, None)
    assert (b.value, b.category, b.reason) == (None, None, 'no_height')


def test_bmi_ignores_other_users_weights(user, make_user):
    services.confirm_plan(make_user(), REFERENCE)
    assert selectors.bmi_on(user, TODAY, None).reason == 'no_weight'
