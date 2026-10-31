"""
Health API (/api/v1/health/). Thin: reads through health.selectors, writes through
health.services, always scoped to request.user.
"""

from django.http import Http404
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status, viewsets
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tracking.dates import user_today
from config.idempotency import SCHEMA_PARAMETER as IDEMPOTENCY_KEY
from config.idempotency import idempotent

from .. import selectors, services
from ..models import Food, Meal, MealItem
from .serializers import (
    CurrentPlanSerializer,
    CustomFoodInputSerializer,
    DailyNutritionSerializer,
    DateQuerySerializer,
    FoodSearchQuerySerializer,
    FoodSerializer,
    HealthPlanSerializer,
    MealInputSerializer,
    MealItemInputSerializer,
    MealItemUpdateSerializer,
    MealSerializer,
    MealUpdateSerializer,
    PlanInputSerializer,
    TargetsSerializer,
    WeightEntrySerializer,
)


def _valid(serializer_class, data, request=None, **kwargs):
    serializer = serializer_class(data=data, context={'request': request}, **kwargs)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _date(request):
    return _valid(DateQuerySerializer, request.query_params).get('date') or user_today(request.user)


def _inputs(request):
    serializer = PlanInputSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.to_inputs()


class PlanPreviewView(APIView):
    """Calculate the targets for these inputs ("Calculate" button). Saves nothing."""

    @extend_schema(request=PlanInputSerializer, responses=TargetsSerializer)
    def post(self, request):
        return Response(TargetsSerializer(services.preview_plan(_inputs(request))).data)


class PlanView(APIView):
    """The current plan (GET) and confirming a new one (POST, "Confirm" button)."""

    @extend_schema(responses=CurrentPlanSerializer)
    def get(self, request):
        plan = selectors.current_plan(request.user)
        if plan is None:
            raise NotFound('No plan yet.')
        return Response(CurrentPlanSerializer(plan).data)

    @extend_schema(request=PlanInputSerializer, responses={201: CurrentPlanSerializer})
    def post(self, request):
        """
        Save the plan, effective today: a new version (or today's version updated).
        Also records today's weight and updates the water/sleep/steps/activity trackables.
        """
        plan = services.confirm_plan(request.user, _inputs(request))
        return Response(CurrentPlanSerializer(plan).data, status=status.HTTP_201_CREATED)


class PlanHistoryView(generics.ListAPIView):
    """Every version of the plan, newest first."""

    serializer_class = HealthPlanSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):  # schema generation: no user
            return selectors.plan_history(None).none()
        return selectors.plan_history(self.request.user).select_related('user')


class WeightView(generics.ListAPIView):
    """Weight history, newest first (GET), and logging a weight (POST)."""

    serializer_class = WeightEntrySerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return selectors.weights_for(None).none()
        return selectors.weights_for(self.request.user)

    @extend_schema(request=WeightEntrySerializer, responses={200: WeightEntrySerializer})
    def post(self, request):
        """Record a weight. One per day: logging again that day replaces it."""
        serializer = WeightEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        entry = services.log_weight(request.user, data.get('date') or user_today(request.user), data['weight_kg'])
        return Response(WeightEntrySerializer(entry).data)


# ----------------------------- foods --------------------------------------- #
class FoodViewSet(viewsets.GenericViewSet):
    """
    The food database: search (global + the user's own foods) and the user's custom
    foods. Only custom foods can be edited or deleted; global foods are managed by staff.
    """

    serializer_class = FoodSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Food.objects.none()
        if self.action in ('partial_update', 'destroy'):
            return selectors.custom_foods_for(self.request.user)
        return selectors.foods_for(self.request.user).prefetch_related('portions')

    @extend_schema(parameters=[FoodSearchQuerySerializer], responses=FoodSerializer(many=True))
    def list(self, request):
        """Search names and aliases in ar/en/fr. Best matches first, at most `limit` results."""
        params = _valid(FoodSearchQuerySerializer, request.query_params)
        foods = selectors.search_foods(request.user, params['q'], params['limit'])
        return Response(FoodSerializer(foods, many=True).data)

    def retrieve(self, request, pk=None):
        return Response(FoodSerializer(self.get_object()).data)

    @extend_schema(request=CustomFoodInputSerializer, responses={201: FoodSerializer})
    def create(self, request):
        """Create a private custom food (visible only to you)."""
        food = services.create_custom_food(request.user, **_valid(CustomFoodInputSerializer, request.data))
        return Response(FoodSerializer(food).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=CustomFoodInputSerializer, responses=FoodSerializer)
    def partial_update(self, request, pk=None):
        """Edit one of your custom foods. Past meals keep their snapshot."""
        data = _valid(CustomFoodInputSerializer, request.data, partial=True)
        food = services.update_custom_food(request.user, self.get_object(), **data)
        return Response(FoodSerializer(food).data)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        """Delete one of your custom foods. Past meals keep their snapshot."""
        services.delete_custom_food(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)


# ----------------------------- meals --------------------------------------- #
class MealViewSet(viewsets.GenericViewSet):
    """Meals of a day (manual logging). Today and yesterday only for every change."""

    serializer_class = MealSerializer

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Meal.objects.none()
        return Meal.objects.filter(user=self.request.user)

    def _reloaded(self, meal):
        return selectors.meals_for(self.request.user, meal.date).get(pk=meal.pk)

    @extend_schema(parameters=[DateQuerySerializer], responses=MealSerializer(many=True))
    def list(self, request):
        """The meals of one day (default today), with items and per-meal totals."""
        return Response(MealSerializer(selectors.meals_for(request.user, _date(request)), many=True).data)

    @extend_schema(request=MealInputSerializer, responses={201: MealSerializer}, parameters=[IDEMPOTENCY_KEY])
    @idempotent
    def create(self, request):
        data = _valid(MealInputSerializer, request.data, request)
        meal = services.log_meal(
            request.user, date=data.get('date') or user_today(request.user), meal_type=data['meal_type'],
            note=data['note'], items=data['items'], source=Meal.Source.MANUAL,
        )
        return Response(MealSerializer(self._reloaded(meal)).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=MealUpdateSerializer, responses=MealSerializer)
    def partial_update(self, request, pk=None):
        meal = services.update_meal(request.user, self.get_object(), **_valid(MealUpdateSerializer, request.data))
        return Response(MealSerializer(self._reloaded(meal)).data)

    @extend_schema(responses={204: None})
    def destroy(self, request, pk=None):
        services.delete_meal(request.user, self.get_object())
        return Response(status=status.HTTP_204_NO_CONTENT)


class _MealItemMixin:
    """Item changes return the whole meal (items + totals): the app updates the calorie ring at once."""

    def _meal(self, request, pk):
        try:
            return Meal.objects.get(pk=pk, user=request.user)
        except Meal.DoesNotExist:
            raise Http404 from None

    def _item(self, request, pk, item_pk):
        try:
            return MealItem.objects.select_related('meal').get(pk=item_pk, meal_id=pk, meal__user=request.user)
        except MealItem.DoesNotExist:
            raise Http404 from None

    def _meal_response(self, request, meal, status_code=status.HTTP_200_OK):
        meal = selectors.meals_for(request.user, meal.date).get(pk=meal.pk)
        return Response(MealSerializer(meal).data, status=status_code)


class MealItemsView(_MealItemMixin, APIView):
    @extend_schema(request=MealItemInputSerializer, responses={201: MealSerializer},
                   parameters=[IDEMPOTENCY_KEY])
    @idempotent
    def post(self, request, pk):
        """Add an item to a meal. Returns the updated meal."""
        meal = self._meal(request, pk)
        services.add_item(request.user, meal, _valid(MealItemInputSerializer, request.data, request))
        return self._meal_response(request, meal, status.HTTP_201_CREATED)


class MealItemDetailView(_MealItemMixin, APIView):
    @extend_schema(request=MealItemUpdateSerializer, responses=MealSerializer)
    def patch(self, request, pk, item_pk):
        """Change the food, portion/quantity or grams (the snapshot is recomputed). Returns the updated meal."""
        item = self._item(request, pk, item_pk)
        services.update_item(request.user, item, **_valid(MealItemUpdateSerializer, request.data, request))
        return self._meal_response(request, item.meal)

    @extend_schema(responses=MealSerializer)
    def delete(self, request, pk, item_pk):
        """Remove an item (not the last one: delete the meal instead). Returns the updated meal (200)."""
        item = self._item(request, pk, item_pk)
        services.remove_item(request.user, item)
        return self._meal_response(request, item.meal)


class NutritionView(APIView):
    """Daily totals vs the plan's targets: the calories ring and macro cards."""

    @extend_schema(parameters=[DateQuerySerializer], responses=DailyNutritionSerializer)
    def get(self, request):
        return Response(DailyNutritionSerializer(selectors.daily_nutrition(request.user, _date(request))).data)
