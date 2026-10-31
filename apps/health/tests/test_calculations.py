"""Plan calculations: pure functions, no database."""

from dataclasses import replace
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.health.calculations import ActivityLevel, Goal, Mode, PlanInputs, Sex, bmr, calculate_targets, tdee

# Reference case from the design: male, 80 kg, 178 cm, 24 years, light activity, cut.
REFERENCE = PlanInputs(
    goal=Goal.CUT, sex=Sex.MALE, weight_kg=Decimal(80), height_cm=Decimal(178), age=24,
    activity_level=ActivityLevel.LIGHT,
)


def test_reference_case():
    assert bmr(Sex.MALE, 80, 178, 24) == Decimal('1797.5')
    assert tdee(Sex.MALE, 80, 178, 24, ActivityLevel.LIGHT) == Decimal('2471.5625')
    t = calculate_targets(REFERENCE)
    # 2471.56 x 0.85 = 2100.8 -> 2100. Fat 525/9 = 58.3 -> 58. Carbs (2100-640-522)/4 = 234.5 -> 235.
    assert (t.calories, t.protein_g, t.fat_g, t.carbs_g, t.water_ml) == (2100, 160, 58, 235, 4000)
    assert (t.steps, t.activity_minutes, t.sleep_hours) == (8000, 30, Decimal(8))
    assert t.calorie_floor_applied is False


@pytest.mark.parametrize(('goal', 'calories'), [
    (Goal.CUT, 2100),        # 2471.56 x 0.85 = 2100.8
    (Goal.MAINTAIN, 2470),   # 2471.56
    (Goal.BULK, 2720),       # 2471.56 x 1.10 = 2718.7
])
def test_each_goal(goal, calories):
    assert calculate_targets(replace(REFERENCE, goal=goal)).calories == calories


@pytest.mark.parametrize(('level', 'calories'), [
    (ActivityLevel.SEDENTARY, 1830),    # 1797.5 x 1.2   x 0.85 = 1833.5
    (ActivityLevel.LIGHT, 2100),        # 1797.5 x 1.375 x 0.85 = 2100.8
    (ActivityLevel.MODERATE, 2370),     # 1797.5 x 1.55  x 0.85 = 2368.2
    (ActivityLevel.ACTIVE, 2640),       # 1797.5 x 1.725 x 0.85 = 2635.6
    (ActivityLevel.VERY_ACTIVE, 2900),  # 1797.5 x 1.9   x 0.85 = 2903.0
])
def test_each_activity_level(level, calories):
    assert calculate_targets(replace(REFERENCE, activity_level=level)).calories == calories


def test_female_formula():
    inputs = replace(REFERENCE, sex=Sex.FEMALE, weight_kg=Decimal(65), height_cm=Decimal(165), age=30,
                     goal=Goal.MAINTAIN, activity_level=ActivityLevel.MODERATE)
    # 650 + 1031.25 - 150 - 161 = 1370.25 x 1.55 = 2123.9 -> 2120
    assert calculate_targets(inputs).calories == 2120


def test_calorie_floor_female():
    # 500 + 1000 - 300 - 161 = 1039 x 1.2 x 0.85 = 1059.8 -> 1060 < 1200
    inputs = replace(REFERENCE, sex=Sex.FEMALE, weight_kg=Decimal(50), height_cm=Decimal(160), age=60,
                     activity_level=ActivityLevel.SEDENTARY)
    t = calculate_targets(inputs)
    assert (t.calories, t.calorie_floor_applied) == (1200, True)


def test_calorie_floor_male():
    # 500 + 937.5 - 450 + 5 = 992.5 x 1.2 x 0.85 = 1012 -> 1010 < 1500
    inputs = replace(REFERENCE, weight_kg=Decimal(50), height_cm=Decimal(150), age=90,
                     activity_level=ActivityLevel.SEDENTARY)
    t = calculate_targets(inputs)
    assert (t.calories, t.calorie_floor_applied) == (1500, True)


def test_water_rounds_to_nearest_250():
    assert calculate_targets(replace(REFERENCE, weight_kg=Decimal(72))).water_ml == 3500    # 3600 -> 3500
    assert calculate_targets(replace(REFERENCE, weight_kg=Decimal('72.5'))).water_ml == 3750  # 3625 -> 3750


def test_manual_mode():
    t = calculate_targets(replace(REFERENCE, mode=Mode.MANUAL, calories=2000, protein_g=150))
    # fat 500/9 = 55.6 -> 56 (504 kcal); carbs (2000 - 600 - 504) / 4 = 224
    assert (t.calories, t.protein_g, t.fat_g, t.carbs_g) == (2000, 150, 56, 224)
    assert t.calorie_floor_applied is False


def test_manual_mode_floor():
    t = calculate_targets(replace(REFERENCE, mode=Mode.MANUAL, calories=1300, protein_g=100))
    assert (t.calories, t.calorie_floor_applied) == (1500, True)


def test_manual_water():
    assert calculate_targets(replace(REFERENCE, water_mode=Mode.MANUAL, water_ml=2750)).water_ml == 2750


def test_protein_up_to_75_percent_is_accepted():
    t = calculate_targets(replace(REFERENCE, mode=Mode.MANUAL, calories=2000, protein_g=375))  # exactly 75%
    assert t.carbs_g == 0


def test_protein_above_75_percent_is_refused():
    with pytest.raises(ValidationError) as exc:
        calculate_targets(replace(REFERENCE, mode=Mode.MANUAL, calories=2000, protein_g=376))
    assert 'protein_g' in exc.value.message_dict


@pytest.mark.parametrize(('changes', 'field'), [
    ({'age': 17}, 'age'),
    ({'age': 101}, 'age'),
    ({'weight_kg': Decimal('29.9')}, 'weight_kg'),
    ({'weight_kg': Decimal(301)}, 'weight_kg'),
    ({'height_cm': Decimal(119)}, 'height_cm'),
    ({'height_cm': Decimal(231)}, 'height_cm'),
    ({'goal': 'lose'}, 'goal'),
    ({'sex': 'other'}, 'sex'),
    ({'activity_level': 'extreme'}, 'activity_level'),
    ({'mode': 'magic'}, 'mode'),
    ({'mode': Mode.MANUAL, 'protein_g': 100}, 'calories'),             # missing calories
    ({'mode': Mode.MANUAL, 'calories': 1199, 'protein_g': 100}, 'calories'),
    ({'mode': Mode.MANUAL, 'calories': 6001, 'protein_g': 100}, 'calories'),
    ({'mode': Mode.MANUAL, 'calories': 2000}, 'protein_g'),            # missing protein
    ({'water_mode': Mode.MANUAL}, 'water_ml'),                         # missing water
    ({'water_mode': Mode.MANUAL, 'water_ml': 499}, 'water_ml'),
])
def test_invalid_inputs(changes, field):
    with pytest.raises(ValidationError) as exc:
        calculate_targets(replace(REFERENCE, **changes))
    assert field in exc.value.message_dict


def test_manual_values_are_ignored_in_auto_mode():
    t = calculate_targets(replace(REFERENCE, calories=1000, protein_g=10, water_ml=1))
    assert (t.calories, t.protein_g, t.water_ml) == (2100, 160, 4000)
