from datetime import date, timedelta

import pytest
from rest_framework.test import APIClient

from apps.health import selectors, services
from apps.health.models import HealthMetric, HealthPlan, WeightEntry
from apps.tracking.models import Trackable

from .test_calculations import REFERENCE

pytestmark = pytest.mark.django_db

TODAY = date(2026, 3, 10)
BASE = '/api/v1/health'
REFERENCE_BODY = {
    'goal': 'cut', 'sex': 'male', 'weight_kg': '80', 'height_cm': '178', 'age': 24, 'activity_level': 'light',
}


@pytest.fixture(autouse=True)
def today(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def api(user):
    return client_for(user)


@pytest.fixture
def other(make_user):
    return make_user()


def test_requires_authentication():
    for path in ('plan/', 'plan/history/', 'weight/'):
        assert APIClient().get(f'{BASE}/{path}').status_code == 401
    assert APIClient().post(f'{BASE}/plan/preview/', REFERENCE_BODY, format='json').status_code == 401


# ----------------------------- preview ------------------------------------- #
def test_preview_reference_case(api):
    r = api.post(f'{BASE}/plan/preview/', REFERENCE_BODY, format='json')
    assert r.status_code == 200
    assert r.json() == {
        'calories': 2100, 'protein_g': 160, 'carbs_g': 235, 'fat_g': 58, 'water_ml': 4000,
        'steps': 8000, 'activity_minutes': 30, 'sleep_hours': '8.0', 'calorie_floor_applied': False,
    }


def test_preview_writes_nothing(api):
    api.post(f'{BASE}/plan/preview/', REFERENCE_BODY, format='json')
    api.post(f'{BASE}/plan/preview/', {**REFERENCE_BODY, 'mode': 'manual', 'calories': 2000, 'protein_g': 150},
             format='json')
    assert not HealthPlan.objects.exists()
    assert not WeightEntry.objects.exists()
    assert not HealthMetric.objects.exists()
    assert not Trackable.objects.exists()


def test_preview_manual_mode(api):
    body = {**REFERENCE_BODY, 'mode': 'manual', 'calories': 2000, 'protein_g': 150,
            'water_mode': 'manual', 'water_ml': 3000}
    data = api.post(f'{BASE}/plan/preview/', body, format='json').json()
    assert (data['calories'], data['protein_g'], data['fat_g'], data['carbs_g'], data['water_ml']) == (
        2000, 150, 56, 224, 3000)


def test_preview_returns_the_floor_flag(api):
    body = {**REFERENCE_BODY, 'mode': 'manual', 'calories': 1300, 'protein_g': 100}
    data = api.post(f'{BASE}/plan/preview/', body, format='json').json()
    assert (data['calories'], data['calorie_floor_applied']) == (1500, True)


@pytest.mark.parametrize(('changes', 'field'), [
    ({'age': 17}, 'age'),
    ({'weight_kg': '29'}, 'weight_kg'),
    ({'height_cm': '250'}, 'height_cm'),
    ({'goal': 'lose'}, 'goal'),
    ({'mode': 'manual', 'calories': 2000, 'protein_g': 400}, 'protein_g'),  # > 75% of calories
    ({'mode': 'manual', 'protein_g': 100}, 'calories'),
    ({'age': 'twenty'}, 'age'),
])
def test_preview_invalid_inputs(api, changes, field):
    r = api.post(f'{BASE}/plan/preview/', {**REFERENCE_BODY, **changes}, format='json')
    assert r.status_code == 400
    assert field in r.json()


# ----------------------------- confirm / current --------------------------- #
def test_no_plan_yet(api):
    assert api.get(f'{BASE}/plan/').status_code == 404


def test_confirm_and_get_current(api, user):
    r = api.post(f'{BASE}/plan/', REFERENCE_BODY, format='json')
    assert r.status_code == 201
    data = r.json()
    assert (data['effective_from'], data['calories'], data['age'], data['birth_year']) == ('2026-03-10', 2100, 24,
                                                                                           2002)
    assert (data['weight_kg'], data['bmi']) == ('80.00', '25.2')
    assert api.get(f'{BASE}/plan/').json() == data
    assert Trackable.objects.filter(user=user, managed_by='health').count() == 4


def test_confirm_twice_does_not_duplicate_trackables(api, user):
    api.post(f'{BASE}/plan/', REFERENCE_BODY, format='json')
    api.post(f'{BASE}/plan/', {**REFERENCE_BODY, 'water_mode': 'manual', 'water_ml': 2500}, format='json')
    assert Trackable.objects.filter(user=user).count() == 4
    assert selectors.metric_trackables(user)['water'].target_value == 2500


def test_confirm_invalid_saves_nothing(api):
    assert api.post(f'{BASE}/plan/', {**REFERENCE_BODY, 'age': 15}, format='json').status_code == 400
    assert not HealthPlan.objects.exists()


def test_history_newest_first(api, user, freeze_utc):
    freeze_utc(2026, 3, 9, 12, 0)
    services.confirm_plan(user, REFERENCE)
    freeze_utc(2026, 3, 10, 12, 0)
    api.post(f'{BASE}/plan/', {**REFERENCE_BODY, 'goal': 'bulk'}, format='json')
    results = api.get(f'{BASE}/plan/history/').json()['results']
    assert [(p['effective_from'], p['goal']) for p in results] == [('2026-03-10', 'bulk'), ('2026-03-09', 'cut')]


# ----------------------------- weight -------------------------------------- #
def test_log_weight_defaults_to_today(api):
    r = api.post(f'{BASE}/weight/', {'weight_kg': '79.5'}, format='json')
    assert r.status_code == 200
    assert (r.json()['date'], r.json()['weight_kg']) == ('2026-03-10', '79.50')


def test_log_weight_same_day_replaces(api, user):
    api.post(f'{BASE}/weight/', {'weight_kg': '80'}, format='json')
    api.post(f'{BASE}/weight/', {'weight_kg': '79'}, format='json')
    assert WeightEntry.objects.get(user=user).weight_kg == 79


def test_log_weight_window(api):
    old = (TODAY - timedelta(days=31)).isoformat()
    r = api.post(f'{BASE}/weight/', {'weight_kg': '80', 'date': old}, format='json')
    assert r.status_code == 400 and 'date' in r.json()
    future = (TODAY + timedelta(days=1)).isoformat()
    assert api.post(f'{BASE}/weight/', {'weight_kg': '80', 'date': future}, format='json').status_code == 400


def test_weight_list_newest_first(api, user):
    services.log_weight(user, TODAY - timedelta(days=2), 81)
    services.log_weight(user, TODAY, 80)
    results = api.get(f'{BASE}/weight/').json()['results']
    assert [w['date'] for w in results] == ['2026-03-10', '2026-03-08']


# ----------------------------- isolation ----------------------------------- #
def test_other_users_data_is_never_visible(api, user, other):
    services.confirm_plan(other, REFERENCE)
    services.log_weight(other, TODAY, 70)

    assert api.get(f'{BASE}/plan/').status_code == 404
    assert api.get(f'{BASE}/plan/history/').json()['results'] == []
    assert api.get(f'{BASE}/weight/').json()['results'] == []


def test_other_users_metric_trackables_give_404(api, other):
    services.confirm_plan(other, REFERENCE)
    water = selectors.metric_trackables(other)['water']
    assert api.get(f'/api/v1/tracking/trackables/{water.pk}/').status_code == 404
    assert api.patch(f'/api/v1/tracking/trackables/{water.pk}/', {'name': 'x'}, format='json').status_code == 404


def test_metric_target_cannot_be_edited_through_tracking_api(api, user):
    api.post(f'{BASE}/plan/', REFERENCE_BODY, format='json')
    water = selectors.metric_trackables(user)['water']
    r = api.patch(f'/api/v1/tracking/trackables/{water.pk}/', {'target_value': '1000'}, format='json')
    assert r.status_code == 400
    assert api.post(f'/api/v1/tracking/trackables/{water.pk}/archive/').status_code == 400
