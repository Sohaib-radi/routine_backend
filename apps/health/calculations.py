"""
Plan targets: pure functions, no database. Every constant lives at the top of this
module so the formula can be read (and changed) in one place.

Calories: Mifflin-St Jeor BMR x activity factor, then the goal adjustment, rounded
to the nearest 10 and never below the safety floor (1500 kcal men, 1200 women).
Protein 2 g/kg. Fat 25% of calories. Carbs the remainder. Water 50 ml/kg, rounded
to the nearest 250 ml.

Manual mode: the user sets calories and protein; the floor still applies and fat
and carbs are derived the same way.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import models


class Goal(models.TextChoices):
    CUT = 'cut', 'Cut'
    MAINTAIN = 'maintain', 'Maintain'
    BULK = 'bulk', 'Bulk'


class Sex(models.TextChoices):
    MALE = 'male', 'Male'
    FEMALE = 'female', 'Female'


class ActivityLevel(models.TextChoices):
    SEDENTARY = 'sedentary', 'Sedentary'
    LIGHT = 'light', 'Light'
    MODERATE = 'moderate', 'Moderate'
    ACTIVE = 'active', 'Active'
    VERY_ACTIVE = 'very_active', 'Very active'


class Mode(models.TextChoices):
    AUTO = 'auto', 'Calculated'
    MANUAL = 'manual', 'Manual'


# ----------------------------- constants ----------------------------------- #
ACTIVITY_FACTORS = {
    ActivityLevel.SEDENTARY: Decimal('1.2'),
    ActivityLevel.LIGHT: Decimal('1.375'),
    ActivityLevel.MODERATE: Decimal('1.55'),
    ActivityLevel.ACTIVE: Decimal('1.725'),
    ActivityLevel.VERY_ACTIVE: Decimal('1.9'),
}
GOAL_ADJUSTMENTS = {  # multiplier applied to TDEE
    Goal.CUT: Decimal('0.85'),       # -15%
    Goal.MAINTAIN: Decimal('1.00'),
    Goal.BULK: Decimal('1.10'),      # +10%
}
# Mifflin-St Jeor: 10 x kg + 6.25 x cm - 5 x age + s
MIFFLIN_SEX_CONSTANT = {Sex.MALE: Decimal(5), Sex.FEMALE: Decimal(-161)}
CALORIE_FLOOR = {Sex.MALE: 1500, Sex.FEMALE: 1200}
CALORIE_ROUNDING = 10

PROTEIN_G_PER_KG = Decimal(2)
FAT_SHARE = Decimal('0.25')
MAX_PROTEIN_SHARE = Decimal('0.75')  # above this, fat (25%) + protein leave no room for carbs
KCAL_PER_G = {'protein': 4, 'carbs': 4, 'fat': 9}

WATER_ML_PER_KG = 50
WATER_ROUNDING_ML = 250

DEFAULT_STEPS = 8000
DEFAULT_ACTIVITY_MINUTES = 30
DEFAULT_SLEEP_HOURS = Decimal(8)

# Accepted inputs
AGE_RANGE = (18, 100)  # calorie deficit plans are not appropriate for minors
WEIGHT_KG_RANGE = (Decimal(30), Decimal(300))
HEIGHT_CM_RANGE = (Decimal(120), Decimal(230))
MANUAL_CALORIES_RANGE = (1200, 6000)
MANUAL_PROTEIN_G_RANGE = (1, 500)
MANUAL_WATER_ML_RANGE = (500, 10_000)


# ----------------------------- data ---------------------------------------- #
@dataclass(frozen=True)
class PlanInputs:
    goal: str
    sex: str
    weight_kg: Decimal
    height_cm: Decimal
    age: int
    activity_level: str
    mode: str = Mode.AUTO
    water_mode: str = Mode.AUTO
    calories: int | None = None     # manual mode
    protein_g: int | None = None    # manual mode
    water_ml: int | None = None     # manual water


@dataclass(frozen=True)
class Targets:
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int
    water_ml: int
    steps: int
    activity_minutes: int
    sleep_hours: Decimal
    calorie_floor_applied: bool  # the formula (or the user) went below the floor


# ----------------------------- formulas ------------------------------------ #
def _round(value: Decimal, step: int = 1) -> int:
    return int((Decimal(value) / step).quantize(Decimal(1), rounding=ROUND_HALF_UP)) * step


def bmr(sex: str, weight_kg: Decimal, height_cm: Decimal, age: int) -> Decimal:
    """Mifflin-St Jeor basal metabolic rate, kcal/day (unrounded)."""
    return (10 * Decimal(weight_kg) + Decimal('6.25') * Decimal(height_cm) - 5 * age
            + MIFFLIN_SEX_CONSTANT[sex])


def tdee(sex, weight_kg, height_cm, age, activity_level) -> Decimal:
    """Total daily energy expenditure (unrounded)."""
    return bmr(sex, weight_kg, height_cm, age) * ACTIVITY_FACTORS[activity_level]


def apply_floor(sex: str, calories: int) -> tuple[int, bool]:
    floor = CALORIE_FLOOR[sex]
    return (floor, True) if calories < floor else (calories, False)


def auto_calories(sex, weight_kg, height_cm, age, activity_level, goal) -> tuple[int, bool]:
    raw = tdee(sex, weight_kg, height_cm, age, activity_level) * GOAL_ADJUSTMENTS[goal]
    return apply_floor(sex, _round(raw, CALORIE_ROUNDING))


def auto_protein_g(weight_kg: Decimal) -> int:
    return _round(PROTEIN_G_PER_KG * Decimal(weight_kg))


def auto_water_ml(weight_kg: Decimal) -> int:
    return _round(WATER_ML_PER_KG * Decimal(weight_kg), WATER_ROUNDING_ML)


def fat_and_carbs(calories: int, protein_g: int) -> tuple[int, int]:
    """Fat = 25% of calories; carbs = what remains after protein and the rounded fat."""
    protein_kcal = protein_g * KCAL_PER_G['protein']
    if protein_kcal > MAX_PROTEIN_SHARE * calories:
        raise ValidationError({'protein_g': f'Protein cannot exceed {MAX_PROTEIN_SHARE:.0%} of calories.'})
    fat_g = _round(FAT_SHARE * calories / KCAL_PER_G['fat'])
    remainder = calories - protein_kcal - fat_g * KCAL_PER_G['fat']
    return fat_g, max(0, _round(Decimal(remainder) / KCAL_PER_G['carbs']))


# ----------------------------- validation ---------------------------------- #
def _check_range(errors, field, value, bounds, unit=''):
    low, high = bounds
    if value is None or not (low <= value <= high):
        errors[field] = f'Must be between {low} and {high}{unit}.'


def validate(inputs: PlanInputs) -> None:
    errors = {}
    for field, enum in (('goal', Goal), ('sex', Sex), ('activity_level', ActivityLevel),
                        ('mode', Mode), ('water_mode', Mode)):
        if getattr(inputs, field) not in enum.values:
            errors[field] = f'Unknown value “{getattr(inputs, field)}”.'
    _check_range(errors, 'age', inputs.age, AGE_RANGE)
    _check_range(errors, 'weight_kg', inputs.weight_kg, WEIGHT_KG_RANGE, ' kg')
    _check_range(errors, 'height_cm', inputs.height_cm, HEIGHT_CM_RANGE, ' cm')
    if inputs.mode == Mode.MANUAL:
        _check_range(errors, 'calories', inputs.calories, MANUAL_CALORIES_RANGE, ' kcal')
        _check_range(errors, 'protein_g', inputs.protein_g, MANUAL_PROTEIN_G_RANGE, ' g')
    if inputs.water_mode == Mode.MANUAL:
        _check_range(errors, 'water_ml', inputs.water_ml, MANUAL_WATER_ML_RANGE, ' ml')
    if errors:
        raise ValidationError(errors)


# ----------------------------- entry point --------------------------------- #
def calculate_targets(inputs: PlanInputs) -> Targets:
    """Validate the inputs and compute every target of the plan."""
    validate(inputs)
    if inputs.mode == Mode.AUTO:
        calories, floored = auto_calories(inputs.sex, inputs.weight_kg, inputs.height_cm, inputs.age,
                                          inputs.activity_level, inputs.goal)
        protein_g = auto_protein_g(inputs.weight_kg)
    else:
        calories, floored = apply_floor(inputs.sex, inputs.calories)
        protein_g = inputs.protein_g
    fat_g, carbs_g = fat_and_carbs(calories, protein_g)
    water_ml = auto_water_ml(inputs.weight_kg) if inputs.water_mode == Mode.AUTO else inputs.water_ml
    return Targets(
        calories=calories, protein_g=protein_g, carbs_g=carbs_g, fat_g=fat_g, water_ml=water_ml,
        steps=DEFAULT_STEPS, activity_minutes=DEFAULT_ACTIVITY_MINUTES, sleep_hours=DEFAULT_SLEEP_HOURS,
        calorie_floor_applied=floored,
    )
