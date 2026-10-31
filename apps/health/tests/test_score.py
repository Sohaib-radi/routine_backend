"""Health score: pure functions."""

from decimal import Decimal

import pytest

from apps.health.score import (
    IN_PROGRESS,
    PAUSED,
    Metric,
    calorie_points,
    day_score,
    health_score,
    metric_points,
    percent,
)

TARGETS = {'water': 4000, 'sleep': 8, 'steps': 8000, 'activity': 30}
KCAL = 2000


def metrics(paused=(), **values):
    return {name: Metric(Decimal(values.get(name, 0)), Decimal(target), name in paused)
            for name, target in TARGETS.items()}


def score(eaten=KCAL, is_today=False, paused=(), **values):
    return health_score(calories=Metric(Decimal(eaten), Decimal(KCAL)), metrics=metrics(paused, **values),
                        is_today=is_today)


# ----------------------------- metrics ------------------------------------- #
@pytest.mark.parametrize(('value', 'target', 'points'), [
    (0, 4000, 0),
    (1500, 4000, 38),   # 37.5 -> 38 (half up)
    (4000, 4000, 100),
    (6000, 4000, 100),  # capped
    (Decimal('6.5'), 8, 81),  # 81.25
    (4000, 8000, 50),
    (45, 30, 100),
])
def test_metric_points(value, target, points):
    assert metric_points(value, target) == points


def test_percent_is_not_capped():
    assert percent(6000, 4000) == 150
    assert percent(1, 0) == 0


# ----------------------------- calories ------------------------------------ #
@pytest.mark.parametrize(('eaten', 'points'), [
    (2000, 100),   # on target
    (1800, 100),   # -10%: edge of the full band
    (2200, 100),   # +10%
    (1600, 75),    # -20%: (0.5 - 0.2) / 0.4 = 75
    (2400, 75),    # +20%: symmetric
    (2600, 50),    # +30%
    (1300, 38),    # -35%: 37.5 -> 38
    (1000, 0),     # -50%
    (3000, 0),     # +50%
    (4000, 0),     # beyond
    (0, 0),        # nothing logged (past date)
])
def test_calorie_curve(eaten, points):
    assert calorie_points(eaten, KCAL) == points


def test_overeating_never_beats_eating_on_target():
    on_target = calorie_points(KCAL, KCAL)
    for eaten in range(KCAL, 2 * KCAL + 1, 50):
        assert calorie_points(eaten, KCAL) <= on_target
    for a, b in zip(range(2200, 4000, 50), range(2250, 4001, 50)):
        assert calorie_points(b, KCAL) <= calorie_points(a, KCAL)  # more food, never more points


# ----------------------------- total --------------------------------------- #
def test_total_is_the_average_of_every_metric():
    s = score(eaten=2000, water=4000, sleep=8, steps=8000, activity=30)
    assert s.total == 100
    assert s.breakdown == {'calories': 100, 'water': 100, 'sleep': 100, 'steps': 100, 'activity': 100}
    assert s.excluded == {}


def test_total_rounds_half_up():
    # calories 100, water 50, sleep 0, steps 0, activity 100 -> 250 / 5 = 50
    assert score(eaten=2000, water=2000, activity=30).total == 50
    # 100 + 38 + 0 + 0 + 0 = 138 / 5 = 27.6 -> 28
    assert score(eaten=2000, water=1500).total == 28
    # steps 200 / 8000 = 2.5% -> 3 points: 100 + 50 + 0 + 3 + 0 = 153 / 5 = 30.6 -> 31
    assert score(eaten=2000, water=2000, steps=200).total == 31


def test_total_half_exactly_rounds_up():
    # 4 metrics (one paused): 100 + 100 + 1 + 1 = 202 / 4 = 50.5 -> 51
    s = score(eaten=2000, water=4000, sleep=Decimal('0.08'), steps=80, paused=('activity',))
    assert (s.breakdown['sleep'], s.breakdown['steps'], s.total) == (1, 1, 51)


def test_paused_metrics_are_excluded():
    s = score(eaten=2000, water=4000, sleep=8, paused=('steps', 'activity'))
    assert s.total == 100  # calories, water, sleep only
    assert s.excluded == {'steps': PAUSED, 'activity': PAUSED}
    assert set(s.breakdown) == {'calories', 'water', 'sleep'}


def test_a_skipped_day_counts_as_zero():
    """The caller passes 0 for a skipped day: it lowers the score like a missed one."""
    assert score(eaten=2000, water=4000, sleep=8, steps=8000, activity=0).total == 80


# ----------------------------- today --------------------------------------- #
def test_morning_with_no_meals():
    s = score(eaten=0, is_today=True, sleep=8)
    assert s.excluded == {'calories': IN_PROGRESS}
    assert s.breakdown == {'water': 0, 'sleep': 100, 'steps': 0, 'activity': 0}
    assert s.total == 25


def test_after_breakfast():
    s = score(eaten=500, is_today=True, sleep=7, water=1000)
    assert s.excluded == {'calories': IN_PROGRESS}
    assert s.total == 28  # (25 + 88 + 0 + 0) / 4 = 28.25


def test_up_to_ten_percent_over_is_still_in_progress_today():
    assert score(eaten=2200, is_today=True).excluded == {'calories': IN_PROGRESS}


def test_over_target_before_evening_uses_the_curve():
    s = score(eaten=2600, is_today=True, sleep=8)  # +30%: overeating can't be undone
    assert 'calories' not in s.excluded
    assert s.breakdown['calories'] == 50


@pytest.mark.parametrize(('eaten', 'calories'), [(0, 0), (500, 0), (2200, 100), (2600, 50)])
def test_same_values_on_a_past_date_use_the_full_curve(eaten, calories):
    s = score(eaten=eaten, is_today=False, sleep=8)
    assert s.excluded == {}
    assert s.breakdown['calories'] == calories


def test_everything_excluded_gives_no_total():
    s = score(eaten=0, is_today=True, paused=('water', 'sleep', 'steps', 'activity'))
    assert (s.total, s.breakdown) == (None, {})


# ----------------------------- no score ------------------------------------ #
def test_no_plan_gives_no_score():
    assert day_score(has_plan=False, everything_paused=False, calories=Metric(Decimal(0), Decimal(KCAL)),
                     metrics=metrics(), is_today=False) is None


def test_everything_paused_gives_no_score():
    assert day_score(has_plan=True, everything_paused=True, calories=Metric(Decimal(0), Decimal(KCAL)),
                     metrics=metrics(), is_today=False) is None


def test_day_score_otherwise():
    s = day_score(has_plan=True, everything_paused=False, calories=Metric(Decimal(2000), Decimal(KCAL)),
                  metrics=metrics(water=4000, sleep=8, steps=8000, activity=30), is_today=False)
    assert s.total == 100
