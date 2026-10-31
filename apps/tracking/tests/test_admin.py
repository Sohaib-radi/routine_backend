from datetime import date

import pytest
from django.urls import reverse

from apps.tracking import services
from apps.tracking.tests.test_services import log, pause
from apps.tracking.models import StreakCache, Trackable

pytestmark = pytest.mark.django_db


@pytest.fixture
def data(user):
    t = services.create_trackable(user, name='Water', kind=Trackable.Kind.QUANTITY, target_value=8,
                                  start_date=date(2026, 1, 1))
    checkin = log(user, t, date(2026, 1, 2), value=3)
    pause_ = pause(user, start_date=date(2026, 1, 5))
    cache = services.refresh_streak_cache(t.pk)
    return t, checkin, pause_, cache


@pytest.fixture
def admin_client(client, make_user):
    from apps.accounts.models import User

    client.force_login(User.objects.create_superuser('admin@example.com', 'S3cure-pass!'))
    return client


def test_pages_render(admin_client, data):
    t, checkin, pause, cache = data
    for name, obj in (('trackable', t), ('checkin', checkin), ('pause', pause), ('streakcache', cache)):
        assert admin_client.get(reverse(f'admin:tracking_{name}_changelist')).status_code == 200
        assert admin_client.get(reverse(f'admin:tracking_{name}_change', args=[obj.pk])).status_code == 200
    assert admin_client.get(reverse('admin:tracking_trackable_add')).status_code == 200


@pytest.mark.parametrize('name', ['checkin', 'pause', 'streakcache'])
def test_history_and_derived_data_are_read_only(admin_client, name):
    assert admin_client.get(reverse(f'admin:tracking_{name}_add')).status_code == 403


def test_checkin_cannot_be_edited(admin_client, data):
    _, checkin, _, _ = data
    url = reverse('admin:tracking_checkin_change', args=[checkin.pk])
    admin_client.post(url, {'value': '999', 'status': 'done'})
    checkin.refresh_from_db()
    assert checkin.value == 3


def test_recompute_action_rebuilds_cache(admin_client, data):
    t, _, _, cache = data
    StreakCache.objects.filter(pk=cache.pk).update(longest=42)
    res = admin_client.post(reverse('admin:tracking_streakcache_changelist'),
                            {'action': 'recompute', '_selected_action': [cache.pk]})
    assert res.status_code == 302
    cache.refresh_from_db()
    assert cache.longest == 0  # 3 of 8 glasses is partial: rebuilt from source

