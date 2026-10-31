"""
Health API serializers. They check shapes and types; the accepted ranges and every
rule live in health.calculations / health.services.
"""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.tracking.api.serializers import OwnedPrimaryKeyField
from apps.tracking.dates import user_today

from .. import selectors
from ..calculations import ActivityLevel, Goal, Mode, PlanInputs, Sex
from ..models import Food, FoodPortion, HealthPlan, Meal, MealItem, PortionUnit, WeightEntry


class PlanInputSerializer(serializers.Serializer):
    """What the plan screen sends, for preview and confirm."""

    goal = serializers.ChoiceField(choices=Goal.choices)
    sex = serializers.ChoiceField(choices=Sex.choices)
    weight_kg = serializers.DecimalField(max_digits=5, decimal_places=2)
    height_cm = serializers.DecimalField(max_digits=4, decimal_places=1)
    age = serializers.IntegerField(help_text='Years (18-100). Stored as a birth year.')
    activity_level = serializers.ChoiceField(choices=ActivityLevel.choices)
    mode = serializers.ChoiceField(choices=Mode.choices, default=Mode.AUTO,
                                   help_text='auto: calculated. manual: send calories and protein_g.')
    water_mode = serializers.ChoiceField(choices=Mode.choices, default=Mode.AUTO,
                                         help_text='auto: 50 ml/kg. manual: send water_ml.')
    calories = serializers.IntegerField(required=False, allow_null=True, help_text='Manual mode (1200-6000).')
    protein_g = serializers.IntegerField(required=False, allow_null=True, help_text='Manual mode.')
    water_ml = serializers.IntegerField(required=False, allow_null=True, help_text='Manual water.')

    def to_inputs(self) -> PlanInputs:
        return PlanInputs(**self.validated_data)


class TargetsSerializer(serializers.Serializer):
    calories = serializers.IntegerField()
    protein_g = serializers.IntegerField()
    carbs_g = serializers.IntegerField()
    fat_g = serializers.IntegerField()
    water_ml = serializers.IntegerField()
    steps = serializers.IntegerField()
    activity_minutes = serializers.IntegerField()
    sleep_hours = serializers.DecimalField(max_digits=3, decimal_places=1)
    calorie_floor_applied = serializers.BooleanField(
        help_text='True when the calories were raised to the safety floor (1500 men, 1200 women): '
                  'show the user a message.',
    )


class HealthPlanSerializer(serializers.ModelSerializer):
    age = serializers.SerializerMethodField(help_text="From the birth year, on the user's today.")

    class Meta:
        model = HealthPlan
        fields = [
            'id', 'effective_from', 'goal', 'sex', 'age', 'birth_year', 'height_cm', 'activity_level',
            'mode', 'water_mode', 'calories', 'protein_g', 'carbs_g', 'fat_g', 'water_ml', 'steps',
            'activity_minutes', 'sleep_hours', 'calorie_floor_applied', 'created_at', 'updated_at',
        ]
        read_only_fields = fields

    def get_age(self, plan) -> int:
        return selectors.age_on(plan.birth_year, user_today(plan.user))


class CurrentPlanSerializer(HealthPlanSerializer):
    """The current plan, with the latest weight and the BMI computed from it."""

    weight_kg = serializers.SerializerMethodField(help_text='Latest recorded weight.')
    bmi = serializers.SerializerMethodField(help_text='From the latest weight and height. Never stored.')

    class Meta(HealthPlanSerializer.Meta):
        fields = [*HealthPlanSerializer.Meta.fields, 'weight_kg', 'bmi']
        read_only_fields = fields

    def get_weight_kg(self, plan) -> str | None:
        entry = selectors.latest_weight(plan.user)
        return None if entry is None else str(entry.weight_kg)

    def get_bmi(self, plan) -> str | None:
        value = selectors.current_bmi(plan.user, plan)
        return None if value is None else str(value)


class WeightEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = WeightEntry
        fields = ['id', 'date', 'weight_kg', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
        extra_kwargs = {
            'date': {'required': False, 'help_text': "Last 30 days, no future dates. Defaults to the user's today."},
        }
        validators = []  # (user, date) uniqueness: a second log that day replaces the weight


# ----------------------------- foods --------------------------------------- #
class UnitLabelsSerializer(serializers.Serializer):
    ar = serializers.CharField()
    en = serializers.CharField()
    fr = serializers.CharField()


class FoodPortionSerializer(serializers.ModelSerializer):
    labels = UnitLabelsSerializer(read_only=True, help_text='The unit name in each language.')

    class Meta:
        model = FoodPortion
        fields = ['unit', 'grams', 'labels']


class FoodSerializer(serializers.ModelSerializer):
    """A food as the apps show it. Nutrition is per 100 g."""

    portions = FoodPortionSerializer(many=True, read_only=True)
    is_custom = serializers.SerializerMethodField(help_text="True for the user's own custom food.")

    class Meta:
        model = Food
        fields = ['id', 'name_ar', 'name_en', 'name_fr', 'aliases', 'category', 'kcal', 'protein_g', 'carbs_g',
                  'fat_g', 'source', 'is_verified', 'is_custom', 'portions', 'created_at', 'updated_at']
        read_only_fields = fields

    def get_is_custom(self, food) -> bool:
        return food.user_id is not None


class PortionInputSerializer(serializers.Serializer):
    unit = serializers.ChoiceField(choices=PortionUnit.choices)
    grams = serializers.DecimalField(max_digits=6, decimal_places=1)


class CustomFoodInputSerializer(serializers.Serializer):
    """Create (POST) or edit (PATCH) a custom food. Ranges are checked by health.services."""

    name_ar = serializers.CharField(max_length=120, required=False, allow_blank=True)
    name_en = serializers.CharField(max_length=120, required=False, allow_blank=True)
    name_fr = serializers.CharField(max_length=120, required=False, allow_blank=True)
    aliases = serializers.ListField(child=serializers.CharField(max_length=120), required=False, max_length=20)
    category = serializers.ChoiceField(choices=Food.Category.choices, required=False)
    kcal = serializers.DecimalField(max_digits=5, decimal_places=1, help_text='Per 100 g.')
    protein_g = serializers.DecimalField(max_digits=5, decimal_places=1, help_text='Per 100 g.')
    carbs_g = serializers.DecimalField(max_digits=5, decimal_places=1, help_text='Per 100 g.')
    fat_g = serializers.DecimalField(max_digits=5, decimal_places=1, help_text='Per 100 g.')
    portions = PortionInputSerializer(many=True, required=False, help_text='PATCH: replaces all portions.')


class FoodSearchQuerySerializer(serializers.Serializer):
    q = serializers.CharField(help_text='At least 2 characters. Any language; Arabic spelling is normalized.')
    limit = serializers.IntegerField(required=False, min_value=1, max_value=selectors.SEARCH_MAX_LIMIT,
                                     default=selectors.SEARCH_DEFAULT_LIMIT)


# ----------------------------- meals --------------------------------------- #
class NutrientsSerializer(serializers.Serializer):
    kcal = serializers.DecimalField(max_digits=8, decimal_places=1)
    protein_g = serializers.DecimalField(max_digits=7, decimal_places=1)
    carbs_g = serializers.DecimalField(max_digits=7, decimal_places=1)
    fat_g = serializers.DecimalField(max_digits=7, decimal_places=1)


class MealItemSerializer(serializers.ModelSerializer):
    """An item as logged. Names and nutrition are the snapshot taken when it was logged."""

    food_id = serializers.UUIDField(read_only=True, allow_null=True, help_text='Null if the food was deleted.')
    portion = serializers.CharField(source='portion_unit', read_only=True, help_text='Empty when grams were entered.')

    class Meta:
        model = MealItem
        fields = ['id', 'food_id', 'name_ar', 'name_en', 'name_fr', 'portion', 'quantity', 'grams', 'kcal',
                  'protein_g', 'carbs_g', 'fat_g', 'created_at', 'updated_at']
        read_only_fields = fields


class MealSerializer(serializers.ModelSerializer):
    items = MealItemSerializer(many=True, read_only=True)
    totals = serializers.SerializerMethodField()

    class Meta:
        model = Meal
        fields = ['id', 'date', 'meal_type', 'note', 'source', 'items', 'totals', 'created_at', 'updated_at']
        read_only_fields = fields

    @extend_schema_field(NutrientsSerializer)
    def get_totals(self, meal):
        return NutrientsSerializer(selectors.meal_totals(meal)).data


class MealItemInputSerializer(serializers.Serializer):
    """Either a portion (with an optional quantity, default 1) or grams."""

    food_id = OwnedPrimaryKeyField(selectors.foods_for, source='food')
    portion = serializers.ChoiceField(choices=PortionUnit.choices, required=False)
    quantity = serializers.DecimalField(max_digits=6, decimal_places=2, required=False,
                                        help_text='Number of portions (0.25-50). Default 1.')
    grams = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, help_text='1-5000 g.')


class MealItemUpdateSerializer(serializers.Serializer):
    food_id = OwnedPrimaryKeyField(selectors.foods_for, source='food', required=False)
    portion = serializers.ChoiceField(choices=PortionUnit.choices, required=False, allow_null=True)
    quantity = serializers.DecimalField(max_digits=6, decimal_places=2, required=False, allow_null=True)
    grams = serializers.DecimalField(max_digits=7, decimal_places=2, required=False, allow_null=True)


class MealInputSerializer(serializers.Serializer):
    date = serializers.DateField(required=False, help_text="Today or yesterday. Defaults to the user's today.")
    meal_type = serializers.ChoiceField(choices=Meal.Type.choices)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True, default='')
    items = MealItemInputSerializer(many=True, min_length=1)


class MealUpdateSerializer(serializers.Serializer):
    date = serializers.DateField(required=False, help_text='Today or yesterday.')
    meal_type = serializers.ChoiceField(choices=Meal.Type.choices, required=False)
    note = serializers.CharField(max_length=500, required=False, allow_blank=True)


class DateQuerySerializer(serializers.Serializer):
    date = serializers.DateField(required=False, help_text="Defaults to the user's today.")


class DailyNutritionSerializer(serializers.Serializer):
    date = serializers.DateField()
    totals = NutrientsSerializer(help_text='From the meals of that day.')
    targets = NutrientsSerializer(allow_null=True, help_text='From the plan that applied that day. Null: no plan.')
    remaining = NutrientsSerializer(allow_null=True, help_text='targets - totals; negative when over.')


# ----------------------------- today (dashboard) --------------------------- #
class TodayPlanSerializer(serializers.ModelSerializer):
    """The targets of the plan that applied on that date."""

    class Meta:
        model = HealthPlan
        fields = ['id', 'effective_from', 'goal', 'calories', 'protein_g', 'carbs_g', 'fat_g', 'water_ml', 'steps',
                  'activity_minutes', 'sleep_hours', 'calorie_floor_applied']
        read_only_fields = fields


class NutrientLineSerializer(serializers.Serializer):
    eaten = serializers.DecimalField(max_digits=8, decimal_places=1)
    target = serializers.DecimalField(max_digits=8, decimal_places=1, allow_null=True)
    remaining = serializers.DecimalField(max_digits=8, decimal_places=1, allow_null=True,
                                         help_text='Negative when over the target.')
    percent = serializers.IntegerField(allow_null=True, help_text='eaten / target, not capped.')


class TodayNutritionSerializer(serializers.Serializer):
    kcal = NutrientLineSerializer()
    protein_g = NutrientLineSerializer()
    carbs_g = NutrientLineSerializer()
    fat_g = NutrientLineSerializer()


class MetricStatusSerializer(serializers.Serializer):
    value = serializers.DecimalField(max_digits=10, decimal_places=2, help_text='0 when nothing logged or skipped.')
    target = serializers.DecimalField(max_digits=10, decimal_places=2, help_text="From that day's plan.")
    unit = serializers.CharField()
    percent = serializers.IntegerField(help_text='value / target, not capped.')
    trackable_id = serializers.UUIDField(help_text='+ button: POST tracking/checkins/ with this trackable.')
    checkin_id = serializers.UUIDField(allow_null=True, help_text='- button: PATCH (or DELETE at 0) '
                                                                 'tracking/checkins/{id}/.')
    checkin_status = serializers.CharField(allow_null=True, help_text='done, partial or skipped.')
    paused = serializers.BooleanField()


class TodayMetricsSerializer(serializers.Serializer):
    water = MetricStatusSerializer()
    sleep = MetricStatusSerializer(help_text='Logged on the day you wake up.')
    steps = MetricStatusSerializer()
    activity = MetricStatusSerializer()


class BmiSerializer(serializers.Serializer):
    value = serializers.DecimalField(max_digits=4, decimal_places=1, allow_null=True)
    category = serializers.ChoiceField(choices=selectors.BMI_CATEGORY_CHOICES, allow_null=True,
                                       help_text='< 18.5 underweight, < 25 normal, < 30 overweight, >= 30 obese.')
    reason = serializers.ChoiceField(choices=['no_weight', 'no_height'], allow_null=True,
                                     help_text='Why there is no value.')


class ScoreSerializer(serializers.Serializer):
    total = serializers.IntegerField(allow_null=True, help_text='0-100. Null when every metric is excluded.')
    breakdown = serializers.DictField(child=serializers.IntegerField(),
                                      help_text='Points (0-100) of each included metric: calories, water, sleep, '
                                                'steps, activity.')
    excluded = serializers.DictField(child=serializers.CharField(),
                                     help_text='Excluded metric -> reason: "paused", or "in_progress" for '
                                               "today's calories while eaten <= target + 10%.")


class HealthTodaySerializer(serializers.Serializer):
    date = serializers.DateField()
    editable = serializers.BooleanField(help_text='Today or yesterday; older dates are read-only.')
    needs_plan = serializers.SerializerMethodField(help_text='No plan that day: show "set up your plan".')
    plan = TodayPlanSerializer(allow_null=True)
    nutrition = TodayNutritionSerializer()
    metrics = TodayMetricsSerializer(allow_null=True, help_text='Null without a plan.')
    everything_paused = serializers.BooleanField()
    bmi = BmiSerializer()
    score = ScoreSerializer(allow_null=True, help_text='Null without a plan or when everything is paused.')

    def get_needs_plan(self, today) -> bool:
        return today.plan is None
