"""
Admin for the ai app (Unfold).

- Feature configuration and provider prices are editable. Every change is recorded
  in the admin history with WHO, WHEN and the old -> new values.
- API keys are never stored or shown: the admin only says whether the provider's
  environment variable is set.
- Usage and meal drafts are read-only; "Daily usage" shows totals per day.
"""

from datetime import timedelta

from django.conf import settings
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from unfold.admin import ModelAdmin
from unfold.decorators import display

from . import config
from .models import AIFeatureConfig, AIUsage, MealDraft, ProviderPrice

DAILY_DAYS = 30


class ValueHistoryMixin:
    """Admin history ("History" button) records old -> new for every changed field."""

    def construct_change_message(self, request, form, formsets, add=False):
        if add or not form.changed_data:
            return super().construct_change_message(request, form, formsets, add)
        changes = [f'{form.fields[name].label}: {form.initial.get(name)!s} → {form.cleaned_data.get(name)!s}'
                   for name in form.changed_data]
        return [{'changed': {'fields': changes}}]


class ReadOnlyAdmin(ModelAdmin):
    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AIFeatureConfig)
class AIFeatureConfigAdmin(ValueHistoryMixin, ModelAdmin):
    """Without a row, a feature uses the environment defaults (AI_* settings)."""

    list_display = ['feature', 'enabled', 'provider', 'model', 'effort', 'daily_limit', 'api_key', 'updated_by',
                    'updated_at']
    list_filter = ['enabled', 'provider']
    fields = ['feature', 'enabled', 'provider', 'model', 'effort', 'daily_limit', 'api_key', 'updated_by',
              'updated_at']
    readonly_fields = ['api_key', 'updated_by', 'updated_at']

    @display(description=_('API key'))
    def api_key(self, obj):
        if obj is None or not obj.provider:
            return '-'
        if config.provider_key_configured(obj.provider):
            return _('Configured')
        variable = settings.AI_PROVIDER_KEY_ENV.get(obj.provider, '?')
        return _('Missing: set %(variable)s in the environment') % {'variable': variable}

    def save_model(self, request, obj, form, change):
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)  # the post_save signal invalidates the cache
        if not config.provider_key_configured(obj.provider):
            messages.warning(request, _('No API key is configured for this provider: analyses will fail.'))
        if config.price_per_mtok(obj.provider, obj.model) is None:
            messages.warning(request, _('No price is set for this model: usage cost will be empty.'))


@admin.register(ProviderPrice)
class ProviderPriceAdmin(ValueHistoryMixin, ModelAdmin):
    list_display = ['provider', 'model', 'input_usd_per_mtok', 'output_usd_per_mtok', 'updated_at']
    list_filter = ['provider']
    search_fields = ['model']


@admin.register(AIUsage)
class AIUsageAdmin(ReadOnlyAdmin):
    list_display = ['created_at', 'feature', 'provider', 'model', 'success', 'error_code', 'input_tokens',
                    'output_tokens', 'cost_usd', 'latency_ms', 'user']
    list_filter = ['feature', 'provider', 'model', 'success', 'error_code']
    list_select_related = ['user']
    search_fields = ['user__email']
    date_hierarchy = 'created_at'

    def get_urls(self):
        daily = path('daily/', self.admin_site.admin_view(self.daily_view), name='ai_aiusage_daily')
        return [daily, *super().get_urls()]

    def daily_view(self, request):
        """Totals per day (UTC) and feature/model, for the last 30 days."""
        if not self.has_view_permission(request):
            raise PermissionDenied
        since = timezone.now() - timedelta(days=DAILY_DAYS)
        rows = (AIUsage.objects.filter(created_at__gte=since)
                .annotate(day=TruncDate('created_at'))
                .values('day', 'feature', 'provider', 'model')
                .annotate(calls=Count('id'), failures=Count('id', filter=Q(success=False)),
                          input_tokens=Sum('input_tokens'), output_tokens=Sum('output_tokens'),
                          cost_usd=Sum('cost_usd'))
                .order_by('-day', 'feature', 'model'))
        total = AIUsage.objects.filter(created_at__gte=since).aggregate(
            calls=Count('id'), failures=Count('id', filter=Q(success=False)), cost_usd=Sum('cost_usd'))
        context = {**self.admin_site.each_context(request), 'title': _('AI usage per day'), 'rows': rows,
                   'total': total, 'days': DAILY_DAYS, 'opts': self.model._meta}
        return TemplateResponse(request, 'admin/ai/aiusage/daily.html', context)


@admin.register(MealDraft)
class MealDraftAdmin(ReadOnlyAdmin):
    """Read-only: drafts change only through ai.services (the user's edits and confirm)."""

    list_display = ['created_at', 'user', 'status', 'error_code', 'meal_type', 'date', 'model']
    list_filter = ['status', 'error_code', 'meal_type_source', 'model']
    list_select_related = ['user']
    search_fields = ['user__email', 'text']
    date_hierarchy = 'created_at'
