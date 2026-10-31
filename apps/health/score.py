"""
Health score (0-100) of one day: pure functions, no database. Every constant is at
the top. Computed on the fly, never stored, so it always matches the data and the
plan of that date.

- Water, sleep, steps, activity: percent of target, capped at 100.
- Calories: 100 within ±10% of the target, then linear down to 0 at ±50%.
  Today only: while eaten <= target + 10% the day isn't finished, so calories are
  excluded ("in_progress"); above that the normal curve applies (overeating can't
  be undone). Past dates always use the full curve (nothing logged = 0).
- Total: average of the included metrics (equal weights), rounded half up.
- A paused metric is excluded. Everything paused, or no plan: no score at all.
- A skipped metric day counts as 0 (skipping must not raise the score; pausing is
  the honest way to remove a metric).
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

CALORIES = 'calories'
METRICS = ('water', 'sleep', 'steps', 'activity')
ORDER = (CALORIES, *METRICS)

MAX_POINTS = Decimal(100)
CALORIE_FULL_BAND = Decimal('0.10')  # within ±10% of target: 100
CALORIE_ZERO_BAND = Decimal('0.50')  # at ±50% and beyond: 0
WEIGHTS = {name: 1 for name in ORDER}  # equal weights

PAUSED = 'paused'
IN_PROGRESS = 'in_progress'


@dataclass(frozen=True)
class Metric:
    value: Decimal   # 0 when nothing was logged (or the day was skipped)
    target: Decimal
    paused: bool = False


@dataclass(frozen=True)
class Score:
    total: int | None                                       # None when every metric is excluded
    breakdown: dict[str, int] = field(default_factory=dict)  # included metrics only
    excluded: dict[str, str] = field(default_factory=dict)   # metric -> reason


def round_half_up(value) -> int:
    return int(Decimal(value).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def percent(value, target) -> int:
    """value / target in percent, not capped (progress bars can show 120%)."""
    target = Decimal(target)
    return round_half_up(Decimal(value) * 100 / target) if target > 0 else 0


def metric_points(value, target) -> int:
    """Percent of target, capped at 100."""
    return min(percent(value, target), int(MAX_POINTS))


def calorie_points(eaten, target) -> int:
    """100 within ±10% of the target, linear to 0 at ±50%, 0 beyond. Symmetric."""
    target = Decimal(target)
    if target <= 0:
        return 0
    deviation = abs(Decimal(eaten) - target) / target
    if deviation <= CALORIE_FULL_BAND:
        return int(MAX_POINTS)
    if deviation >= CALORIE_ZERO_BAND:
        return 0
    return round_half_up(MAX_POINTS * (CALORIE_ZERO_BAND - deviation) / (CALORIE_ZERO_BAND - CALORIE_FULL_BAND))


def calories_in_progress(eaten, target) -> bool:
    """Today, eating up to target + 10% is still on track: the day isn't finished."""
    return Decimal(eaten) <= Decimal(target) * (1 + CALORIE_FULL_BAND)


def health_score(*, calories: Metric, metrics: dict[str, Metric], is_today: bool) -> Score:
    """
    The score of one day. `calories.paused` means "everything is paused" (calories
    are not a trackable): the caller returns no score in that case, see day_score().
    """
    breakdown, excluded = {}, {}
    if is_today and calories_in_progress(calories.value, calories.target):
        excluded[CALORIES] = IN_PROGRESS
    else:
        breakdown[CALORIES] = calorie_points(calories.value, calories.target)
    for name in METRICS:
        metric = metrics[name]
        if metric.paused:
            excluded[name] = PAUSED
        else:
            breakdown[name] = metric_points(metric.value, metric.target)

    weight = sum(WEIGHTS[name] for name in breakdown)
    total = round_half_up(Decimal(sum(WEIGHTS[n] * p for n, p in breakdown.items())) / weight) if weight else None
    return Score(total=total, breakdown=breakdown, excluded=excluded)


def day_score(*, has_plan: bool, everything_paused: bool, calories: Metric, metrics: dict[str, Metric],
              is_today: bool) -> Score | None:
    """None when there is no plan or everything is paused; otherwise health_score()."""
    if not has_plan or everything_paused:
        return None
    return health_score(calories=calories, metrics=metrics, is_today=is_today)
