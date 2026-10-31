"""
Admin for health (Unfold). Plans, weights and metric links are read-only: plans are
history, and every write goes through health.services (which keeps the metric
trackables in sync). Staff edit the global foods; users' custom foods are read-only.
"""

from django.contrib import admin
from unfold.admin import ModelAdmin, TabularInline

from .models import Food, FoodPortion, HealthMetric, HealthPlan, Meal, MealItem, WeightEntry


class ReadOnlyAdmin(ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(HealthPlan)
class HealthPlanAdmin(ReadOnlyAdmin):
    list_display = ['user', 'effective_from', 'goal', 'mode', 'calories', 'protein_g', 'carbs_g', 'fat_g',
                    'water_ml']
    list_filter = ['goal', 'mode', 'sex']
    list_select_related = ['user']
    search_fields = ['user__email']
    date_hierarchy = 'effective_from'


@admin.register(WeightEntry)
class WeightEntryAdmin(ReadOnlyAdmin):
    list_display = ['user', 'date', 'weight_kg']
    list_select_related = ['user']
    search_fields = ['user__email']
    date_hierarchy = 'date'


@admin.register(HealthMetric)
class HealthMetricAdmin(ReadOnlyAdmin):
    list_display = ['user', 'metric', 'trackable']
    list_filter = ['metric']
    list_select_related = ['user', 'trackable']
    search_fields = ['user__email']


class FoodPortionInline(TabularInline):
    model = FoodPortion
    extra = 0
    fields = ['unit', 'grams']

    def has_add_permission(self, request, obj=None):
        return obj is None or obj.user_id is None

    def has_change_permission(self, request, obj=None):
        return obj is None or obj.user_id is None

    def has_delete_permission(self, request, obj=None):
        return obj is None or obj.user_id is None


class CustomFoodFilter(admin.SimpleListFilter):
    title = 'owner'
    parameter_name = 'owner'

    def lookups(self, request, model_admin):
        return [('global', 'Global'), ('custom', "Users' custom foods")]

    def queryset(self, request, queryset):
        if self.value() == 'global':
            return queryset.filter(user__isnull=True)
        if self.value() == 'custom':
            return queryset.filter(user__isnull=False)
        return queryset


@admin.register(Food)
class FoodAdmin(ModelAdmin):
    """Global foods are created here (owner empty); custom foods belong to users and are read-only."""

    list_display = ['__str__', 'name_ar', 'category', 'kcal', 'protein_g', 'carbs_g', 'fat_g', 'source',
                    'is_verified', 'is_active', 'user']
    list_filter = [CustomFoodFilter, 'category', 'source', 'is_verified', 'is_active']
    list_select_related = ['user']
    search_fields = ['name_ar', 'name_en', 'name_fr', 'key']
    fields = ['name_ar', 'name_en', 'name_fr', 'aliases', 'category', 'kcal', 'protein_g', 'carbs_g', 'fat_g',
              'source', 'key', 'is_verified', 'is_active', 'user', 'created_at', 'updated_at']
    readonly_fields = ['user', 'created_at', 'updated_at']
    inlines = [FoodPortionInline]

    def has_change_permission(self, request, obj=None):
        if obj is not None and obj.user_id is not None:
            return False
        return super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        if obj is not None and obj.user_id is not None:
            return False
        return super().has_delete_permission(request, obj)


class MealItemInline(TabularInline):
    model = MealItem
    extra = 0
    fields = ['name_en', 'name_ar', 'portion_unit', 'quantity', 'grams', 'kcal', 'protein_g', 'carbs_g', 'fat_g']
    readonly_fields = fields
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Meal)
class MealAdmin(ReadOnlyAdmin):
    """Read-only: meals are logged through health.services (date window, snapshots)."""

    list_display = ['user', 'date', 'meal_type', 'source', 'created_at']
    list_filter = ['meal_type', 'source']
    list_select_related = ['user']
    search_fields = ['user__email']
    date_hierarchy = 'date'
    inlines = [MealItemInline]
