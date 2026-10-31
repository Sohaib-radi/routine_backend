from decimal import Decimal

import pytest
from django.contrib.admin.models import LogEntry
from django.test import override_settings
from django.urls import reverse

from apps.ai import config
from apps.ai.models import AIFeatureConfig, AIUsage, ProviderPrice

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_user():
    from apps.accounts.models import User

    return User.objects.create_superuser('admin@example.com', 'S3cure-pass!')


@pytest.fixture
def admin_client(client, admin_user):
    client.force_login(admin_user)
    return client


FORM = {'feature': 'meal_text', 'enabled': 'on', 'provider': 'anthropic', 'model': 'claude-haiku-5-5',
        'effort': 'low', 'daily_limit': 20}


@pytest.mark.parametrize('name', ['aifeatureconfig', 'providerprice', 'aiusage', 'mealdraft'])
def test_pages_render(admin_client, name):
    assert admin_client.get(reverse(f'admin:ai_{name}_changelist')).status_code == 200


@override_settings(AI_FORCE_PROVIDER='')
def test_saving_the_config_applies_immediately_and_records_who(admin_client, admin_user):
    admin_client.post(reverse('admin:ai_aifeatureconfig_add'), FORM)
    row = AIFeatureConfig.objects.get()
    assert row.updated_by == admin_user
    assert config.feature_config('meal_text').model == 'claude-haiku-5-5'

    r = admin_client.post(reverse('admin:ai_aifeatureconfig_change', args=[row.pk]),
                          {**FORM, 'model': 'claude-opus-5-5', 'daily_limit': 5})
    assert r.status_code == 302
    cfg = config.feature_config('meal_text')  # no stale cache
    assert (cfg.model, cfg.daily_limit, cfg.source) == ('claude-opus-5-5', 5, 'admin')

    entry = LogEntry.objects.filter(object_id=str(row.pk)).order_by('-action_time').first()
    message = entry.get_change_message()
    assert entry.user == admin_user
    assert 'claude-haiku-5-5 → claude-opus-5-5' in message and '20 → 5' in message


def test_disabling_from_the_admin(admin_client):
    admin_client.post(reverse('admin:ai_aifeatureconfig_add'), {**FORM, 'enabled': ''})
    assert config.feature_config('meal_text').enabled is False


def test_api_key_is_never_shown(admin_client, monkeypatch):
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'sk-ant-super-secret')
    admin_client.post(reverse('admin:ai_aifeatureconfig_add'), FORM)
    row = AIFeatureConfig.objects.get()
    for url in (reverse('admin:ai_aifeatureconfig_changelist'),
                reverse('admin:ai_aifeatureconfig_change', args=[row.pk])):
        html = admin_client.get(url).content.decode()
        assert 'sk-ant-super-secret' not in html
        assert 'Configured' in html


def test_missing_key_is_reported(admin_client, monkeypatch):
    monkeypatch.delenv('ANTHROPIC_API_KEY', raising=False)
    r = admin_client.post(reverse('admin:ai_aifeatureconfig_add'), FORM, follow=True)
    html = r.content.decode()
    assert 'No API key is configured' in html
    assert 'set ANTHROPIC_API_KEY' in admin_client.get(reverse('admin:ai_aifeatureconfig_changelist')).content.decode()


def test_price_edit_is_recorded_and_applies(admin_client):
    price = ProviderPrice.objects.create(provider='anthropic', model='claude-x', input_usd_per_mtok=1,
                                         output_usd_per_mtok=2)
    assert config.estimate_cost('anthropic', 'claude-x', 1_000_000, 0) == 1
    admin_client.post(reverse('admin:ai_providerprice_change', args=[price.pk]),
                      {'provider': 'anthropic', 'model': 'claude-x', 'input_usd_per_mtok': '3',
                       'output_usd_per_mtok': '2'})
    assert config.estimate_cost('anthropic', 'claude-x', 1_000_000, 0) == 3
    assert '1.0000 → 3' in LogEntry.objects.latest('action_time').get_change_message()


def test_usage_is_read_only(admin_client):
    assert admin_client.get(reverse('admin:ai_aiusage_add')).status_code == 403


def test_daily_totals(admin_client, user):
    for success, cost in ((True, '0.0004'), (True, '0.0003'), (False, None)):
        AIUsage.objects.create(user=user, feature='meal_text', provider='anthropic', model='claude-haiku-5-5',
                               prompt_version='v', input_tokens=1000, output_tokens=100, success=success,
                               cost_usd=Decimal(cost) if cost else None)
    html = admin_client.get(reverse('admin:ai_aiusage_daily')).content.decode()
    assert 'claude-haiku-5-5' in html
    assert '<strong>Calls</strong>: 3' in html.replace(':</strong>', '</strong>:')
    assert '0.000700' in html  # summed cost


def test_daily_totals_need_the_view_permission(client):
    from apps.accounts.models import User

    staff = User.objects.create_user('staff@example.com', 'S3cure-pass!', is_staff=True)
    client.force_login(staff)
    assert client.get(reverse('admin:ai_aiusage_daily')).status_code == 403
