"""AI API serializers: shapes only; every rule lives in ai.services / ai.proposals."""

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.health.api.serializers import NutrientsSerializer
from apps.health.models import Meal, PortionUnit

from ..extraction import MAX_TEXT_LENGTH
from ..models import MealDraft


class CandidatePortionSerializer(serializers.Serializer):
    unit = serializers.ChoiceField(choices=PortionUnit.choices)
    grams = serializers.DecimalField(max_digits=6, decimal_places=1)


class CandidateSerializer(serializers.Serializer):
    """A food the item can be switched to with one tap. Nutrition per 100 g."""

    food_id = serializers.UUIDField()
    name_ar = serializers.CharField()
    name_en = serializers.CharField()
    name_fr = serializers.CharField()
    is_custom = serializers.BooleanField()
    is_verified = serializers.BooleanField()
    kcal = serializers.DecimalField(max_digits=5, decimal_places=1)
    protein_g = serializers.DecimalField(max_digits=5, decimal_places=1)
    carbs_g = serializers.DecimalField(max_digits=5, decimal_places=1)
    fat_g = serializers.DecimalField(max_digits=5, decimal_places=1)
    portions = CandidatePortionSerializer(many=True)


class ProposalItemSerializer(serializers.Serializer):
    id = serializers.UUIDField(help_text='Send it back in PATCH to keep the candidates.')
    name = serializers.CharField(help_text='The food as the user wrote it.')
    status = serializers.ChoiceField(choices=['matched', 'estimated'],
                                     help_text='matched: numbers from the food table. estimated: AI estimate.')
    food_id = serializers.UUIDField(allow_null=True)
    portion = serializers.ChoiceField(choices=PortionUnit.choices, allow_null=True)
    quantity = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
    grams = serializers.DecimalField(max_digits=7, decimal_places=1)
    grams_estimated = serializers.BooleanField(help_text='The grams are an AI estimate (no matching portion).')
    nutrition = NutrientsSerializer(help_text='Preview for these grams; recomputed from the table on confirm.')
    estimate = NutrientsSerializer(allow_null=True, help_text="The AI's estimate per 100 g.")
    candidates = CandidateSerializer(many=True, help_text='Best match first, then up to 3 alternatives.')
    unit = serializers.ChoiceField(choices=PortionUnit.choices, allow_null=True, help_text='Unit the AI read.')
    quantity_said = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)


class DraftErrorSerializer(serializers.Serializer):
    code = serializers.CharField(help_text='invalid_output, no_food_found, timeout, provider_unavailable, '
                                           'refused, ai_disabled, stuck, ...')
    message = serializers.CharField()


class MealDraftSerializer(serializers.ModelSerializer):
    items = serializers.SerializerMethodField()
    totals = serializers.SerializerMethodField()
    error = serializers.SerializerMethodField()
    meal_id = serializers.UUIDField(read_only=True, allow_null=True, help_text='Set once confirmed.')

    class Meta:
        model = MealDraft
        fields = ['id', 'status', 'text', 'date', 'meal_type', 'meal_type_source', 'items', 'totals', 'error',
                  'meal_id', 'expires_at', 'created_at', 'updated_at']
        read_only_fields = fields

    @extend_schema_field(ProposalItemSerializer(many=True))
    def get_items(self, draft):
        return draft.proposal.get('items', [])

    @extend_schema_field(NutrientsSerializer(allow_null=True))
    def get_totals(self, draft):
        return draft.proposal.get('totals')

    @extend_schema_field(DraftErrorSerializer(allow_null=True))
    def get_error(self, draft):
        return {'code': draft.error_code, 'message': draft.error_message} if draft.error_code else None


class MealDraftCreateSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=MAX_TEXT_LENGTH, help_text='What you ate, in any language.')
    date = serializers.DateField(required=False, help_text="Today or yesterday. Defaults to the user's today.")
    meal_type = serializers.ChoiceField(choices=Meal.Type.choices, required=False,
                                        help_text="Default: from the text, else from the user's local time.")


class EstimateInputSerializer(serializers.Serializer):
    kcal = serializers.DecimalField(max_digits=6, decimal_places=1)
    protein_g = serializers.DecimalField(max_digits=6, decimal_places=1)
    carbs_g = serializers.DecimalField(max_digits=6, decimal_places=1)
    fat_g = serializers.DecimalField(max_digits=6, decimal_places=1)


class DraftItemInputSerializer(serializers.Serializer):
    """Either a food (`food_id` + `portion` [+ `quantity`] or `grams`) or an estimated item (`name`, `grams`, `estimate`)."""

    id = serializers.UUIDField(required=False, help_text='An existing item: keeps its candidates.')
    food_id = serializers.UUIDField(required=False)
    portion = serializers.ChoiceField(choices=PortionUnit.choices, required=False)
    quantity = serializers.DecimalField(max_digits=6, decimal_places=2, required=False)
    grams = serializers.DecimalField(max_digits=7, decimal_places=2, required=False)
    name = serializers.CharField(max_length=80, required=False)
    estimate = EstimateInputSerializer(required=False, help_text='Per 100 g, for an estimated item.')


class MealDraftUpdateSerializer(serializers.Serializer):
    items = DraftItemInputSerializer(many=True, required=False, help_text='The full list: replaces the items.')
    date = serializers.DateField(required=False)
    meal_type = serializers.ChoiceField(choices=Meal.Type.choices, required=False)

    def to_service_kwargs(self) -> dict:
        data = dict(self.validated_data)
        if 'items' in data:
            data['items'] = [{k: (str(v) if k in ('id', 'food_id') else v) for k, v in item.items()}
                             for item in data['items']]
        return data


class AIErrorSerializer(serializers.Serializer):
    code = serializers.CharField()
    detail = serializers.CharField()


class DailyLimitErrorSerializer(AIErrorSerializer):
    limit = serializers.IntegerField()
    reset_at = serializers.DateTimeField(help_text="The user's next local midnight.")
