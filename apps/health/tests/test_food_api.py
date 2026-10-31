from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from apps.health import services
from apps.health.models import Food, Meal, MealItem

from .test_calculations import REFERENCE
from .test_meals import egg, koshari  # noqa: F401 (fixtures)

pytestmark = pytest.mark.django_db

TODAY = date(2026, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)
BASE = '/api/v1/health'


@pytest.fixture(autouse=True)
def _setup(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)
    cache.clear()
    yield
    cache.clear()


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


CUSTOM = {'name_ar': 'كيكة ماما', 'name_en': "Mom's cake", 'category': 'sweet', 'kcal': '380', 'protein_g': '5',
          'carbs_g': '50', 'fat_g': '18', 'portions': [{'unit': 'slice', 'grams': '80'}]}


def post_meal(api, *items, **fields):
    body = {'meal_type': 'lunch', 'items': list(items), **fields}
    return api.post(f'{BASE}/meals/', body, format='json')


# ----------------------------- foods --------------------------------------- #
def test_search(api, koshari):
    r = api.get(f'{BASE}/foods/', {'q': 'كشرى'})  # ى → ي
    assert r.status_code == 200
    [food] = r.json()
    assert (food['name_en'], food['is_custom'], food['kcal']) == ('Koshari', False, '150.0')
    assert food['portions'][0] == {'unit': 'small_plate', 'grams': '250.0',
                                   'labels': {'ar': 'طبق صغير', 'en': 'Small plate', 'fr': 'Petite assiette'}}


def test_search_requires_q(api):
    assert api.get(f'{BASE}/foods/').status_code == 400
    assert api.get(f'{BASE}/foods/', {'q': 'ab', 'limit': 51}).status_code == 400


def test_search_query_count_is_fixed(api, user):
    for i in range(10):
        f = Food.objects.create(key=f'bread-{i}', source='nni', name_en=f'Bread {i}', kcal=1, protein_g=0,
                                carbs_g=0, fat_g=0)
        f.portions.create(unit='loaf', grams=100)
    with CaptureQueriesContext(connection) as ctx:
        assert len(api.get(f'{BASE}/foods/', {'q': 'bread'}).json()) == 10
    assert len(ctx) == 2  # foods + portions


def test_food_detail(api, koshari):
    assert api.get(f'{BASE}/foods/{koshari.pk}/').json()['name_ar'] == 'كشري'


def test_custom_food_crud(api, user):
    r = api.post(f'{BASE}/foods/', CUSTOM, format='json')
    assert r.status_code == 201
    data = r.json()
    assert (data['is_custom'], data['source'], data['is_verified']) == (True, 'custom', False)
    pk = data['id']
    r = api.patch(f'{BASE}/foods/{pk}/', {'kcal': '350', 'portions': [{'unit': 'piece', 'grams': '60'}]},
                  format='json')
    assert r.status_code == 200
    assert (r.json()['kcal'], [p['unit'] for p in r.json()['portions']]) == ('350.0', ['piece'])
    assert api.patch(f'{BASE}/foods/{pk}/', {'name_en': 'Cake'}, format='json').json()['portions'][0]['unit'] == \
        'piece'
    assert api.delete(f'{BASE}/foods/{pk}/').status_code == 204
    assert not Food.objects.filter(pk=pk).exists()


def test_custom_food_validation(api):
    r = api.post(f'{BASE}/foods/', {**CUSTOM, 'kcal': '901'}, format='json')
    assert r.status_code == 400 and 'kcal' in r.json()


def test_global_foods_cannot_be_edited_or_deleted(api, koshari):
    assert api.patch(f'{BASE}/foods/{koshari.pk}/', {'kcal': '1'}, format='json').status_code == 404
    assert api.delete(f'{BASE}/foods/{koshari.pk}/').status_code == 404
    koshari.refresh_from_db()
    assert koshari.kcal == 150


def test_custom_food_is_invisible_to_other_users(api, other):
    pk = api.post(f'{BASE}/foods/', CUSTOM, format='json').json()['id']
    them = client_for(other)
    assert them.get(f'{BASE}/foods/', {'q': 'كيكه'}).json() == []
    assert them.get(f'{BASE}/foods/{pk}/').status_code == 404
    assert them.patch(f'{BASE}/foods/{pk}/', {'kcal': '1'}, format='json').status_code == 404
    assert them.delete(f'{BASE}/foods/{pk}/').status_code == 404
    assert post_meal(them, {'food_id': pk, 'grams': '100'}).status_code == 400


# ----------------------------- meals --------------------------------------- #
def test_log_and_list_meals(api, koshari, egg):
    r = post_meal(api, {'food_id': str(koshari.pk), 'portion': 'plate'},
                  {'food_id': str(egg.pk), 'portion': 'piece', 'quantity': '2'}, note='after gym')
    assert r.status_code == 201
    meal = r.json()
    assert (meal['date'], meal['meal_type'], meal['note'], meal['source']) == ('2026-03-10', 'lunch', 'after gym',
                                                                               'manual')
    assert [(i['name_en'], i['portion'], i['grams'], i['kcal']) for i in meal['items']] == [
        ('Koshari', 'plate', '350.0', '525.0'), ('Egg', 'piece', '100.0', '155.0')]
    assert meal['totals'] == {'kcal': '680.0', 'protein_g': '30.5', 'carbs_g': '95.6', 'fat_g': '19.8'}
    assert api.get(f'{BASE}/meals/').json() == [meal]
    assert api.get(f'{BASE}/meals/', {'date': YESTERDAY.isoformat()}).json() == []


def test_log_meal_validation(api, koshari):
    assert post_meal(api).status_code == 400  # no items
    r = post_meal(api, {'food_id': str(koshari.pk), 'portion': 'plate', 'grams': '100'})
    assert r.status_code == 400 and 'items' in r.json()
    r = post_meal(api, {'food_id': str(koshari.pk), 'grams': '10'}, date=(TODAY - timedelta(days=2)).isoformat())
    assert r.status_code == 400 and 'date' in r.json()
    assert not Meal.objects.exists()


def test_post_meal_is_idempotent(api, koshari):
    body = {'meal_type': 'lunch', 'items': [{'food_id': str(koshari.pk), 'portion': 'plate'}]}
    first = api.post(f'{BASE}/meals/', body, format='json', HTTP_IDEMPOTENCY_KEY='k1')
    retry = api.post(f'{BASE}/meals/', body, format='json', HTTP_IDEMPOTENCY_KEY='k1')
    assert retry.status_code == 201 and retry.json() == first.json()
    assert retry.headers['Idempotent-Replayed'] == 'true'
    assert Meal.objects.count() == 1


def test_update_and_delete_meal(api, koshari):
    pk = post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'}).json()['id']
    r = api.patch(f'{BASE}/meals/{pk}/', {'meal_type': 'dinner', 'date': YESTERDAY.isoformat()}, format='json')
    assert r.status_code == 200
    assert (r.json()['meal_type'], r.json()['date'], r.json()['totals']['kcal']) == ('dinner', '2026-03-09', '150.0')
    assert api.delete(f'{BASE}/meals/{pk}/').status_code == 204
    assert not Meal.objects.exists()


def test_items_endpoints_return_the_updated_meal(api, koshari, egg):
    meal = post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'}).json()  # 150 kcal
    url = f'{BASE}/meals/{meal["id"]}/items/'

    r = api.post(url, {'food_id': str(egg.pk), 'portion': 'piece'}, format='json', HTTP_IDEMPOTENCY_KEY='i1')
    assert r.status_code == 201
    added = r.json()
    assert added['id'] == meal['id']
    assert [i['name_en'] for i in added['items']] == ['Koshari', 'Egg']
    assert added['totals']['kcal'] == '227.5'  # 150 + 77.5
    egg_item = added['items'][1]['id']
    replay = api.post(url, {'food_id': str(egg.pk), 'portion': 'piece'}, format='json', HTTP_IDEMPOTENCY_KEY='i1')
    assert (replay.status_code, replay.json()) == (201, added)
    assert MealItem.objects.count() == 2

    r = api.patch(f'{url}{egg_item}/', {'quantity': '3'}, format='json')
    assert r.status_code == 200
    assert (r.json()['items'][1]['grams'], r.json()['items'][1]['kcal']) == ('150.0', '232.5')
    assert r.json()['totals']['kcal'] == '382.5'  # 150 + 232.5
    r = api.patch(f'{url}{egg_item}/', {'grams': '20'}, format='json')
    assert (r.json()['items'][1]['portion'], r.json()['items'][1]['grams'], r.json()['totals']['kcal']) == (
        '', '20.0', '181.0')  # 150 + 31

    r = api.delete(f'{url}{egg_item}/')
    assert r.status_code == 200
    assert ([i['name_en'] for i in r.json()['items']], r.json()['totals']['kcal']) == (['Koshari'], '150.0')

    last = meal['items'][0]['id']
    r = api.delete(f'{url}{last}/')
    assert r.status_code == 400 and 'delete the meal' in str(r.json())


def test_item_responses_match_the_meals_list(api, koshari, egg):
    """The returned meal is exactly what GET meals/ shows, so the app can just swap it in."""
    meal = post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'}).json()
    returned = api.post(f'{BASE}/meals/{meal["id"]}/items/', {'food_id': str(egg.pk), 'grams': '50'},
                        format='json').json()
    assert api.get(f'{BASE}/meals/').json() == [returned]


def test_item_change_query_count_is_fixed(api, user, koshari):
    meal = services.log_meal(user, date=TODAY, meal_type='lunch', items=[{'food': koshari, 'grams': 10}] * 20)
    item = meal.items.first()
    with CaptureQueriesContext(connection) as ctx:
        api.patch(f'{BASE}/meals/{meal.pk}/items/{item.pk}/', {'grams': '30'}, format='json')
    small = len(ctx)
    services.log_meal(user, date=TODAY, meal_type='dinner', items=[{'food': koshari, 'grams': 10}] * 2)
    meal2 = Meal.objects.get(meal_type='dinner')
    item2 = meal2.items.first()
    with CaptureQueriesContext(connection) as ctx:
        api.patch(f'{BASE}/meals/{meal2.pk}/items/{item2.pk}/', {'grams': '30'}, format='json')
    assert len(ctx) == small  # independent of the number of items


def test_old_meals_cannot_be_changed_through_the_api(api, koshari, freeze_utc):
    meal = post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'}).json()
    freeze_utc(2026, 3, 12, 12, 0)
    assert api.patch(f'{BASE}/meals/{meal["id"]}/', {'note': 'x'}, format='json').status_code == 400
    assert api.delete(f'{BASE}/meals/{meal["id"]}/').status_code == 400
    item_url = f'{BASE}/meals/{meal["id"]}/items/{meal["items"][0]["id"]}/'
    assert api.patch(item_url, {'grams': '1'}, format='json').status_code == 400


def test_other_users_meals_and_items_give_404(api, other, koshari):
    meal = post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'},
                     {'food_id': str(koshari.pk), 'grams': '50'}).json()
    item = meal['items'][0]['id']
    them = client_for(other)
    assert them.get(f'{BASE}/meals/').json() == []
    assert them.patch(f'{BASE}/meals/{meal["id"]}/', {'note': 'x'}, format='json').status_code == 404
    assert them.delete(f'{BASE}/meals/{meal["id"]}/').status_code == 404
    assert them.post(f'{BASE}/meals/{meal["id"]}/items/', {'food_id': str(koshari.pk), 'grams': '1'},
                     format='json').status_code == 404
    assert them.patch(f'{BASE}/meals/{meal["id"]}/items/{item}/', {'grams': '1'}, format='json').status_code == 404
    assert them.delete(f'{BASE}/meals/{meal["id"]}/items/{item}/').status_code == 404
    assert MealItem.objects.count() == 2


def test_item_of_another_meal_gives_404(api, koshari):
    a = post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'}).json()
    b = post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'}).json()
    assert api.patch(f'{BASE}/meals/{a["id"]}/items/{b["items"][0]["id"]}/', {'grams': '1'},
                     format='json').status_code == 404


# ----------------------------- nutrition ----------------------------------- #
def test_nutrition_without_plan(api, koshari):
    post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'})
    data = api.get(f'{BASE}/nutrition/').json()
    assert data == {'date': '2026-03-10',
                    'totals': {'kcal': '150.0', 'protein_g': '5.0', 'carbs_g': '27.0', 'fat_g': '2.5'},
                    'targets': None, 'remaining': None}


def test_nutrition_against_the_plan(api, user, koshari):
    services.confirm_plan(user, REFERENCE)
    post_meal(api, {'food_id': str(koshari.pk), 'portion': 'plate'})
    data = api.get(f'{BASE}/nutrition/', {'date': '2026-03-10'}).json()
    assert data['targets'] == {'kcal': '2100.0', 'protein_g': '160.0', 'carbs_g': '235.0', 'fat_g': '58.0'}
    assert data['remaining'] == {'kcal': '1575.0', 'protein_g': '142.5', 'carbs_g': '140.5', 'fat_g': '49.2'}


def test_nutrition_is_per_user(api, other, koshari):
    post_meal(api, {'food_id': str(koshari.pk), 'grams': '100'})
    assert client_for(other).get(f'{BASE}/nutrition/').json()['totals']['kcal'] == '0.0'


# ----------------------------- query counts -------------------------------- #
@pytest.mark.parametrize('meals', [1, 8])
def test_meals_and_nutrition_use_a_fixed_number_of_queries(api, user, koshari, meals):
    services.confirm_plan(user, REFERENCE)
    for _ in range(meals):
        services.log_meal(user, date=TODAY, meal_type='snack', items=[{'food': koshari, 'grams': 10}] * 4)
    with CaptureQueriesContext(connection) as ctx:
        assert len(api.get(f'{BASE}/meals/').json()) == meals
    assert len(ctx) == 2  # meals + items
    with CaptureQueriesContext(connection) as ctx:
        api.get(f'{BASE}/nutrition/')
    assert len(ctx) == 2  # totals + plan
