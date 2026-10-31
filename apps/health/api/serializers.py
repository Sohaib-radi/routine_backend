"""
Health API serializers. They check shapes and types; the accepted ranges and every
rule live in health.calculations / health.services.
"""

from rest_framework import serializers

from apps.tracking.dates import user_today

from .. import selectors
from ..calculations import ActivityLevel, Goal, Mode, PlanInputs, Sex
from ..models import HealthPlan, WeightEntry


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
