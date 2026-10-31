"""
Admin for health (Unfold). Read-only: plans are history, and every write goes
through health.services (which keeps the metric trackables in sync).
"""

from django.contrib import admin
from unfold.admin import ModelAdmin

from .models import HealthMetric, HealthPlan, WeightEntry


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
