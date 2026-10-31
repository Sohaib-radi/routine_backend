from datetime import date

import pytest
from rest_framework.test import APIClient

from apps.ai.models import AIFeatureConfig, AIUsage, MealDraft
from apps.ai.providers import fake
from apps.health.models import Meal

from .conftest import extraction, item
from .test_drafts import BREAKFAST, clock, foods  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db

BASE = '/api/v1/ai/meal-drafts/'
TODAY = date(2026, 3, 10)


def client_for(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


@pytest.fixture
def api(user):
    return client_for(user)


@pytest.fixture
def post(api, django_capture_on_commit_callbacks):
    """POST meal-drafts/ and run the queued analysis (the on-commit Celery call)."""
    def go(body=None, *script, client=None, **headers):
        fake.script(*(script or (BREAKFAST,)))
        with django_capture_on_commit_callbacks(execute=True):
            return (client or api).post(BASE, body or {'text': 'فطرت فول وطعمية وشاي'}, format='json', **headers)
    return go


def test_requires_authentication():
    assert APIClient().post(BASE, {'text': 'فول'}, format='json').status_code == 401


def test_full_flow(api, post, foods):
    r = post()
    assert r.status_code == 202
    created = r.json()
    assert created['status'] == 'pending' and created['items'] == [] and created['error'] is None

    draft = api.get(f'{BASE}{created["id"]}/').json()  # polling
    assert draft['status'] == 'ready'
    assert [i['status'] for i in draft['items']] == ['matched', 'matched', 'matched', 'estimated']
    tameya = draft['items'][1]
    assert (tameya['portion'], tameya['grams'], tameya['nutrition']['kcal']) == ('piece', '60.0', '199.8')
    assert tameya['candidates'][0]['name_en'] == 'Falafel'

    edited = [{'id': i['id'], 'food_id': i['food_id'], 'grams': '100'} for i in draft['items'][:2]]
    r = api.patch(f'{BASE}{draft["id"]}/', {'items': edited, 'meal_type': 'lunch'}, format='json')
    assert r.status_code == 200
    assert (r.json()['meal_type'], r.json()['totals']['kcal']) == ('lunch', '443.0')  # 110 + 333
    assert not Meal.objects.exists()

    r = api.post(f'{BASE}{draft["id"]}/confirm/')
    assert r.status_code == 201
    meal = r.json()
    assert (meal['source'], meal['meal_type'], meal['totals']['kcal']) == ('agent', 'lunch', '443.0')
    assert api.get(f'{BASE}{draft["id"]}/').json()['meal_id'] == meal['id']
    assert api.get('/api/v1/health/meals/').json()[0]['id'] == meal['id']  # a normal meal


def test_confirm_twice(api, post, foods):
    pk = post().json()['id']
    first = api.post(f'{BASE}{pk}/confirm/')
    second = api.post(f'{BASE}{pk}/confirm/')
    assert (first.status_code, second.status_code) == (201, 200)
    assert first.json()['id'] == second.json()['id']
    assert Meal.objects.count() == 1


def test_idempotent_create(api, post, foods):
    first = post(None, BREAKFAST, HTTP_IDEMPOTENCY_KEY='abc')
    retry = post(None, BREAKFAST, HTTP_IDEMPOTENCY_KEY='abc')
    assert retry.json()['id'] == first.json()['id']
    assert MealDraft.objects.count() == 1 and len(fake.calls) == 1


def test_idempotent_confirm(api, post, foods):
    pk = post().json()['id']
    a = api.post(f'{BASE}{pk}/confirm/', HTTP_IDEMPOTENCY_KEY='c1')
    b = api.post(f'{BASE}{pk}/confirm/', HTTP_IDEMPOTENCY_KEY='c1')
    assert (a.status_code, b.status_code, b.headers.get('Idempotent-Replayed')) == (201, 201, 'true')


def test_failed_draft(api, post, foods):
    pk = post(None, 'bad', 'bad').json()['id']
    draft = api.get(f'{BASE}{pk}/').json()
    assert draft['status'] == 'failed'
    assert draft['error']['code'] == 'invalid_output'
    assert api.post(f'{BASE}{pk}/confirm/').status_code == 400


@pytest.mark.parametrize(('body', 'field'), [
    ({'text': ''}, 'text'),
    ({'text': 'x' * 501}, 'text'),
    ({'text': 'فول', 'date': '2026-03-07'}, 'date'),
    ({'text': 'فول', 'meal_type': 'brunch'}, 'meal_type'),
])
def test_create_validation(api, body, field):
    r = api.post(BASE, body, format='json')
    assert r.status_code == 400 and field in r.json()
    assert not MealDraft.objects.exists()


def test_daily_limit_gives_429_with_reset_time(api, post, foods):
    AIFeatureConfig.objects.create(feature='meal_text', provider='fake', model='m', daily_limit=1)
    assert post().status_code == 202
    r = api.post(BASE, {'text': 'فول'}, format='json')
    assert r.status_code == 429
    assert r.json()['code'] == 'daily_limit_exceeded' and r.json()['limit'] == 1
    assert r.json()['reset_at'] == '2026-03-11T00:00:00+00:00'  # the UTC user's next midnight
    assert r.headers['Retry-After'] == str(14 * 3600)          # from 10:00


def test_disabled_feature_gives_503_and_manual_logging_works(api, post, foods):
    pk = post().json()['id']
    AIFeatureConfig.objects.create(feature='meal_text', enabled=False, provider='fake', model='m')
    for r in (api.post(BASE, {'text': 'فول'}, format='json'), api.get(f'{BASE}{pk}/'),
              api.patch(f'{BASE}{pk}/', {'meal_type': 'lunch'}, format='json'), api.post(f'{BASE}{pk}/confirm/')):
        assert r.status_code == 503 and r.json()['code'] == 'ai_disabled'
    r = api.post('/api/v1/health/meals/', {'meal_type': 'lunch',
                                           'items': [{'food_id': str(foods['ful'].pk), 'grams': '100'}]},
                 format='json')
    assert r.status_code == 201


def test_expired_draft(api, post, foods, clock):
    pk = post().json()['id']
    clock(2026, 3, 11, 10, 0)
    assert api.get(f'{BASE}{pk}/').json()['status'] == 'expired'
    r = api.post(f'{BASE}{pk}/confirm/')
    assert r.status_code == 400 and 'expired' in str(r.json())


def test_another_users_draft_gives_404(api, post, foods, make_user):
    pk = post().json()['id']
    them = client_for(make_user())
    assert them.get(f'{BASE}{pk}/').status_code == 404
    assert them.patch(f'{BASE}{pk}/', {'meal_type': 'lunch'}, format='json').status_code == 404
    assert them.post(f'{BASE}{pk}/confirm/').status_code == 404
    assert not Meal.objects.exists()


def test_patch_validation(api, post, foods):
    pk = post().json()['id']
    assert api.patch(f'{BASE}{pk}/', {'items': []}, format='json').status_code == 400
    r = api.patch(f'{BASE}{pk}/', {'items': [{'food_id': str(foods['tameya'].pk), 'portion': 'plate'}]},
                  format='json')
    assert r.status_code == 400 and 'not a portion' in str(r.json())


def test_usage_is_recorded(api, post, foods):
    post()
    post(None, extraction(item('a', kcal=5000)), extraction(item('a', kcal=5000)))
    assert sorted(AIUsage.objects.values_list('success', flat=True)) == [False, False, True]
    assert AIUsage.objects.filter(draft__isnull=False).count() == 3
