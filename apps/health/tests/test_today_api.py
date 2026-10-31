from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.health import selectors, services
from apps.tracking import services as tracking

from .test_calculations import REFERENCE
from .test_meals import koshari  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db

TODAY = date(2026, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)
URL = '/api/v1/health/today/'


@pytest.fixture
def clock(freeze_utc):
    def at(d):
        freeze_utc(d.year, d.month, d.day, 12, 0)
    at(TODAY)
    return at


@pytest.fixture
def api(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def planned(user, clock):
    clock(TODAY - timedelta(days=2))
    services.confirm_plan(user, REFERENCE)
    clock(TODAY)
    return selectors.metric_trackables(user)


def test_requires_authentication():
    assert APIClient().get(URL).status_code == 401


def test_without_plan(api, clock):
    data = api.get(URL).json()
    assert data['needs_plan'] is True
    assert (data['plan'], data['metrics'], data['score']) == (None, None, None)
    assert data['nutrition']['kcal'] == {'eaten': '0.0', 'target': None, 'remaining': None, 'percent': None}
    assert data['bmi'] == {'value': None, 'category': None, 'reason': 'no_weight'}


def test_full_response(api, user, planned, koshari):
    services.log_meal(user, date=TODAY, meal_type='lunch', items=[{'food': koshari, 'portion': 'plate'}])
    water = tracking.log_checkin(user, planned['water'], TODAY, value=1500)
    tracking.log_checkin(user, planned['sleep'], TODAY, value=7)

    data = api.get(URL).json()
    assert (data['date'], data['editable'], data['needs_plan'], data['everything_paused']) == (
        '2026-03-10', True, False, False)
    assert data['plan']['calories'] == 2100 and data['plan']['effective_from'] == '2026-03-08'
    assert data['nutrition']['kcal'] == {'eaten': '525.0', 'target': '2100.0', 'remaining': '1575.0', 'percent': 25}
    assert data['metrics']['water'] == {
        'value': '1500.00', 'target': '4000.00', 'unit': 'ml', 'percent': 38,
        'trackable_id': str(planned['water'].pk), 'checkin_id': str(water.pk), 'checkin_status': 'partial',
        'paused': False,
    }
    assert data['metrics']['steps']['checkin_id'] is None
    assert data['bmi'] == {'value': '25.2', 'category': 'overweight', 'reason': None}
    # calories in progress (525 <= 2310); water 38, sleep 88 (87.5), steps 0, activity 0 -> 126 / 4 = 31.5 -> 32
    assert data['score'] == {'total': 32,
                             'breakdown': {'water': 38, 'sleep': 88, 'steps': 0, 'activity': 0},
                             'excluded': {'calories': 'in_progress'}}


def test_past_date_is_read_only_and_uses_the_full_curve(api, planned):
    data = api.get(URL, {'date': (TODAY - timedelta(days=2)).isoformat()}).json()
    assert data['editable'] is False
    assert data['score']['breakdown']['calories'] == 0
    assert data['score']['excluded'] == {}


def test_future_date_is_refused(api, planned):
    r = api.get(URL, {'date': (TODAY + timedelta(days=1)).isoformat()})
    assert r.status_code == 400 and 'date' in r.json()


def test_invalid_date(api):
    assert api.get(URL, {'date': 'tomorrow'}).status_code == 400


def test_everything_paused(api, user, planned):
    tracking.create_pause(user, start_date=TODAY)
    data = api.get(URL).json()
    assert (data['everything_paused'], data['score']) == (True, None)
    assert all(m['paused'] for m in data['metrics'].values())


def test_other_users_data_never_appears(api, user, planned, make_user, koshari):
    other = make_user()
    services.confirm_plan(other, REFERENCE)
    services.log_meal(other, date=TODAY, meal_type='lunch', items=[{'food': koshari, 'portion': 'plate'}])
    tracking.log_checkin(other, selectors.metric_trackables(other)['water'], TODAY, value=4000)

    data = api.get(URL).json()
    assert data['nutrition']['kcal']['eaten'] == '0.0'
    assert data['metrics']['water']['value'] == '0.00'
    assert data['metrics']['water']['trackable_id'] == str(planned['water'].pk)


@pytest.mark.parametrize('meals', [0, 6])
def test_fixed_number_of_queries(api, user, planned, koshari, meals):
    for _ in range(meals):
        services.log_meal(user, date=TODAY, meal_type='snack', items=[{'food': koshari, 'grams': 10}] * 4)
        tracking.log_checkin(user, planned['water'], TODAY, value=250)
    with CaptureQueriesContext(connection) as ctx:
        assert api.get(URL).status_code == 200
    assert len(ctx) == 6


def test_minus_button_flow(api, user, planned):
    """+ posts a check-in; - patches its value; at 0 the app deletes it (tracking refuses 0)."""
    r = api.post('/api/v1/tracking/checkins/', {'trackable_id': str(planned['water'].pk), 'value': '250'},
                 format='json')
    checkin_id = api.get(URL).json()['metrics']['water']['checkin_id']
    assert checkin_id == r.json()['id']
    assert api.patch(f'/api/v1/tracking/checkins/{checkin_id}/', {'value': '0'}, format='json').status_code == 400
    assert api.delete(f'/api/v1/tracking/checkins/{checkin_id}/').status_code == 204
    water = api.get(URL).json()['metrics']['water']
    assert (water['value'], water['checkin_id']) == ('0.00', None)


def test_sleep_target_and_values_are_decimals(api, user, planned):
    tracking.log_checkin(user, planned['sleep'], TODAY, value=Decimal('7.5'))
    sleep = api.get(URL).json()['metrics']['sleep']
    assert (sleep['value'], sleep['target'], sleep['unit'], sleep['percent']) == ('7.50', '8.00', 'h', 94)
