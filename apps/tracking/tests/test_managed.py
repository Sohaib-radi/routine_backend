"""Managed trackables (managed_by set): only the managing app changes their targets."""

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.tracking import services
from apps.tracking.models import Trackable

pytestmark = pytest.mark.django_db

TODAY = date(2026, 1, 12)


@pytest.fixture(autouse=True)
def today(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)


@pytest.fixture
def water(user):
    return services.create_trackable(user, name='Water', kind=Trackable.Kind.QUANTITY, target_value=2000,
                                     unit='ml', managed_by='health')


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def test_create_records_the_manager(water):
    assert water.managed_by == 'health'


def test_user_trackables_are_not_managed(user):
    assert services.create_trackable(user, name='Read').managed_by == ''


@pytest.mark.parametrize('fields', [
    {'target_value': 3000}, {'unit': 'l'}, {'domain': 'tasks'}, {'end_date': date(2026, 2, 1)},
])
def test_other_callers_cannot_change_managed_fields(user, water, fields):
    with pytest.raises(ValidationError) as exc:
        services.update_trackable(user, water, **fields)
    assert set(exc.value.message_dict) == set(fields)


def test_other_callers_cannot_change_the_schedule(user, water):
    with pytest.raises(ValidationError) as exc:
        services.update_trackable(user, water, schedule={'frequency': 'weekdays', 'weekdays_mask': 1})
    assert 'schedule' in exc.value.message_dict


def test_unchanged_values_are_accepted(user, water):
    """A client sending the whole object back (same target) is not refused."""
    services.update_trackable(user, water, target_value=water.target_value, unit='ml',
                              schedule={'frequency': 'daily'})


def test_other_callers_can_change_presentation(user, water):
    t = services.update_trackable(user, water, name='Hydration', icon='drop', color='#3366FF')
    assert (t.name, t.icon, t.color) == ('Hydration', 'drop', '#3366FF')


def test_the_manager_can_change_targets(user, water):
    t = services.update_trackable(user, water, managed_by='health', target_value=3000)
    assert t.target_value == 3000


def test_another_app_cannot_change_targets(user, water):
    with pytest.raises(ValidationError):
        services.update_trackable(user, water, managed_by='tasks', target_value=3000)


def test_cannot_be_archived_or_unarchived(user, water):
    with pytest.raises(ValidationError):
        services.archive_trackable(user, water)
    with pytest.raises(ValidationError):
        services.unarchive_trackable(user, water)
    water.refresh_from_db()
    assert water.is_archived is False


def test_checkins_work_normally(user, water):
    checkin = services.log_checkin(user, water, TODAY, value=500)
    assert checkin.value == 500


# ----------------------------- API ----------------------------------------- #
def test_api_shows_managed_by(api, water):
    assert api.get(f'/api/v1/tracking/trackables/{water.pk}/').json()['managed_by'] == 'health'


def test_api_cannot_change_the_target(api, water):
    r = api.patch(f'/api/v1/tracking/trackables/{water.pk}/', {'target_value': '3000'}, format='json')
    assert r.status_code == 400
    assert 'target_value' in r.json()
    water.refresh_from_db()
    assert water.target_value == 2000


def test_api_cannot_archive(api, water):
    assert api.post(f'/api/v1/tracking/trackables/{water.pk}/archive/').status_code == 400


def test_api_cannot_set_managed_by(api, user):
    r = api.post('/api/v1/tracking/trackables/', {'name': 'Fake', 'managed_by': 'health'}, format='json')
    assert r.status_code == 201
    assert Trackable.objects.get(pk=r.json()['id']).managed_by == ''


def test_api_can_rename(api, water):
    r = api.patch(f'/api/v1/tracking/trackables/{water.pk}/', {'name': 'Hydration'}, format='json')
    assert r.status_code == 200


# ----------------------------- admin --------------------------------------- #
def test_admin_locks_managed_fields(client, water):
    from apps.accounts.models import User

    admin_user = User.objects.create_superuser('admin@example.com', 'S3cure-pass!')
    client.force_login(admin_user)
    r = client.get(reverse('admin:tracking_trackable_change', args=[water.pk]))
    assert r.status_code == 200
    form = r.context['adminform'].form
    for name in ('target_value', 'unit', 'kind', 'domain', 'start_date', 'end_date', 'is_archived'):
        assert name not in form.fields
    assert 'name' in form.fields
