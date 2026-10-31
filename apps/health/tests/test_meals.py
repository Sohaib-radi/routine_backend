"""Meals: snapshots, portions vs grams, the date window, ownership."""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.health import services
from apps.health.models import Food, FoodPortion, Meal, MealItem

pytestmark = pytest.mark.django_db

TODAY = date(2026, 3, 10)
YESTERDAY = TODAY - timedelta(days=1)


@pytest.fixture(autouse=True)
def today(freeze_utc):
    freeze_utc(TODAY.year, TODAY.month, TODAY.day, 12, 0)


@pytest.fixture
def koshari():
    f = Food.objects.create(key='koshari', source='nni', name_ar='كشري', name_en='Koshari', name_fr='Koshari',
                            kcal=150, protein_g=5, carbs_g=27, fat_g=Decimal('2.5'))
    FoodPortion.objects.create(food=f, unit='plate', grams=350)
    FoodPortion.objects.create(food=f, unit='small_plate', grams=250)
    return f


@pytest.fixture
def egg():
    f = Food.objects.create(key='egg', source='nni', name_ar='بيض', name_en='Egg', name_fr='Œuf',
                            kcal=155, protein_g=13, carbs_g=Decimal('1.1'), fat_g=11)
    FoodPortion.objects.create(food=f, unit='piece', grams=50)
    return f


def lunch(user, *items, d=TODAY, **kwargs):
    return services.log_meal(user, date=d, meal_type='lunch', items=list(items), **kwargs)


def snapshot(item):
    item.refresh_from_db()
    return item.grams, item.kcal, item.protein_g, item.carbs_g, item.fat_g


# ----------------------------- portions vs grams --------------------------- #
def test_portion_snapshot(user, koshari):
    meal = lunch(user, {'food': koshari, 'portion': 'plate'})
    item = meal.items.get()
    # 350 g: 150 x 3.5 = 525 kcal, 5 x 3.5 = 17.5 P, 27 x 3.5 = 94.5 C, 2.5 x 3.5 = 8.75 -> 8.8 F
    assert snapshot(item) == (Decimal(350), Decimal(525), Decimal('17.5'), Decimal('94.5'), Decimal('8.8'))
    assert (item.portion_unit, item.quantity, item.name_ar, item.name_en, item.name_fr) == (
        'plate', Decimal(1), 'كشري', 'Koshari', 'Koshari')


def test_quantity_of_portions(user, egg):
    item = lunch(user, {'food': egg, 'portion': 'piece', 'quantity': 3}).items.get()
    # 3 x 50 g = 150 g: 232.5 kcal, 19.5 P, 1.65 -> 1.7 C (half up), 16.5 F
    assert snapshot(item) == (Decimal(150), Decimal('232.5'), Decimal('19.5'), Decimal('1.7'), Decimal('16.5'))


def test_grams_directly(user, koshari):
    item = lunch(user, {'food': koshari, 'grams': 200}).items.get()
    assert snapshot(item) == (Decimal(200), Decimal(300), Decimal(10), Decimal(54), Decimal(5))
    assert (item.portion_unit, item.quantity, item.portion) == ('', None, None)


def test_portion_and_grams_give_the_same_numbers(user, koshari):
    by_portion = lunch(user, {'food': koshari, 'portion': 'small_plate'}).items.get()
    by_grams = lunch(user, {'food': koshari, 'grams': 250}).items.get()
    assert snapshot(by_portion) == snapshot(by_grams)


@pytest.mark.parametrize(('spec', 'message'), [
    ({'portion': 'plate', 'grams': 100}, 'not both'),
    ({}, 'grams or a portion'),
    ({'portion': 'can'}, 'not a portion'),
    ({'grams': 0}, 'between'),
    ({'grams': 5001}, 'between'),
    ({'portion': 'plate', 'quantity': '0.2'}, 'between'),
    ({'portion': 'plate', 'quantity': 51}, 'between'),
    ({'portion': 'plate', 'quantity': 15}, 'between'),  # 15 x 350 = 5250 g > 5000
])
def test_invalid_items(user, koshari, spec, message):
    with pytest.raises(ValidationError) as exc:
        lunch(user, {'food': koshari, **spec})
    assert message in str(exc.value)
    assert not Meal.objects.exists()


def test_meal_needs_items(user):
    with pytest.raises(ValidationError):
        lunch(user)


def test_item_limit(user, koshari):
    with pytest.raises(ValidationError):
        lunch(user, *[{'food': koshari, 'grams': 10}] * 31)
    meal = lunch(user, *[{'food': koshari, 'grams': 10}] * 30)
    with pytest.raises(ValidationError):
        services.add_item(user, meal, {'food': koshari, 'grams': 10})


def test_several_meals_of_the_same_type_per_day(user, koshari):
    services.log_meal(user, date=TODAY, meal_type='snack', items=[{'food': koshari, 'grams': 10}])
    services.log_meal(user, date=TODAY, meal_type='snack', items=[{'food': koshari, 'grams': 10}])
    assert Meal.objects.filter(user=user, meal_type='snack').count() == 2


def test_invalid_meal_type_and_source(user, koshari):
    with pytest.raises(ValidationError):
        services.log_meal(user, date=TODAY, meal_type='brunch', items=[{'food': koshari, 'grams': 10}])
    with pytest.raises(ValidationError):
        lunch(user, {'food': koshari, 'grams': 10}, source='telepathy')


# ----------------------------- snapshot rule ------------------------------- #
def test_editing_a_food_never_changes_past_meals(user, koshari):
    item = lunch(user, {'food': koshari, 'portion': 'plate'}).items.get()
    before = snapshot(item)
    Food.objects.filter(pk=koshari.pk).update(kcal=999, protein_g=50, name_en='Changed')
    FoodPortion.objects.filter(food=koshari, unit='plate').update(grams=1000)
    assert snapshot(item) == before
    assert item.name_en == 'Koshari'


def test_deleting_a_food_never_changes_past_meals(user, koshari):
    item = lunch(user, {'food': koshari, 'portion': 'plate'}).items.get()
    before = snapshot(item)
    koshari.delete()
    item.refresh_from_db()
    assert (item.food, item.portion, item.portion_unit) == (None, None, 'plate')
    assert snapshot(item) == before


def test_editing_an_item_recomputes_from_the_current_food(user, koshari):
    item = lunch(user, {'food': koshari, 'portion': 'plate'}).items.get()
    Food.objects.filter(pk=koshari.pk).update(kcal=200)
    item = services.update_item(user, item, quantity=2)
    assert (item.grams, item.kcal) == (Decimal(700), Decimal(1400))


def test_editing_an_item_whose_food_was_deleted_rescales(user, koshari):
    item = lunch(user, {'food': koshari, 'grams': 200}).items.get()  # 300 kcal
    koshari.delete()
    item = services.update_item(user, item, grams=100)
    assert (item.grams, item.kcal, item.carbs_g) == (Decimal(100), Decimal(150), Decimal(27))
    with pytest.raises(ValidationError):
        services.update_item(user, item, portion='plate')


def test_update_item_switches_between_portion_and_grams(user, koshari):
    item = lunch(user, {'food': koshari, 'portion': 'plate'}).items.get()
    item = services.update_item(user, item, grams=120)
    assert (item.portion_unit, item.quantity, item.grams) == ('', None, Decimal(120))
    item = services.update_item(user, item, portion='small_plate')
    assert (item.portion_unit, item.quantity, item.grams) == ('small_plate', Decimal(1), Decimal(250))


def test_update_item_changes_food_keeping_the_portion(user, koshari, egg):
    item = lunch(user, {'food': egg, 'grams': 100}).items.get()
    item = services.update_item(user, item, food=koshari)
    assert (item.name_en, item.grams, item.kcal) == ('Koshari', Decimal(100), Decimal(150))


def test_add_and_remove_items(user, koshari, egg):
    meal = lunch(user, {'food': koshari, 'portion': 'plate'})
    item = services.add_item(user, meal, {'food': egg, 'portion': 'piece', 'quantity': 2})
    assert meal.items.count() == 2
    services.remove_item(user, item)
    assert meal.items.count() == 1


def test_cannot_remove_the_last_item(user, koshari):
    item = lunch(user, {'food': koshari, 'grams': 10}).items.get()
    with pytest.raises(ValidationError, match='delete the meal'):
        services.remove_item(user, item)
    assert MealItem.objects.count() == 1


def test_update_and_delete_meal(user, koshari):
    meal = lunch(user, {'food': koshari, 'grams': 10})
    meal = services.update_meal(user, meal, meal_type='dinner', note='late', date=YESTERDAY)
    assert (meal.meal_type, meal.note, meal.date) == ('dinner', 'late', YESTERDAY)
    services.delete_meal(user, meal)
    assert not Meal.objects.exists() and not MealItem.objects.exists()


# ----------------------------- date window --------------------------------- #
def test_today_and_yesterday_are_accepted(user, koshari):
    lunch(user, {'food': koshari, 'grams': 10}, d=TODAY)
    lunch(user, {'food': koshari, 'grams': 10}, d=YESTERDAY)


@pytest.mark.parametrize('d', [TODAY - timedelta(days=2), TODAY + timedelta(days=1)])
def test_older_and_future_dates_are_refused(user, koshari, d):
    with pytest.raises(ValidationError) as exc:
        lunch(user, {'food': koshari, 'grams': 10}, d=d)
    assert 'date' in exc.value.message_dict


def test_window_uses_the_users_local_date(make_user, koshari, freeze_utc):
    cairo = make_user(timezone='Africa/Cairo')
    freeze_utc(2026, 3, 10, 23, 30)  # already March 11 in Cairo
    lunch(cairo, {'food': koshari, 'grams': 10}, d=date(2026, 3, 11))
    with pytest.raises(ValidationError):
        lunch(cairo, {'food': koshari, 'grams': 10}, d=date(2026, 3, 9))


def test_old_meals_are_frozen(user, koshari, freeze_utc):
    meal = lunch(user, {'food': koshari, 'grams': 10})
    item = meal.items.get()
    freeze_utc(2026, 3, 12, 12, 0)  # the meal is now 2 days old
    for call in (lambda: services.update_meal(user, meal, note='x'),
                 lambda: services.delete_meal(user, meal),
                 lambda: services.add_item(user, meal, {'food': koshari, 'grams': 10}),
                 lambda: services.update_item(user, item, grams=20),
                 lambda: services.remove_item(user, item)):
        with pytest.raises(ValidationError):
            call()
    assert MealItem.objects.count() == 1


def test_cannot_move_a_meal_out_of_the_window(user, koshari):
    meal = lunch(user, {'food': koshari, 'grams': 10})
    with pytest.raises(ValidationError):
        services.update_meal(user, meal, date=TODAY - timedelta(days=2))


# ----------------------------- ownership ----------------------------------- #
def test_other_users_meals_and_items_are_unknown(user, make_user, koshari):
    meal = lunch(user, {'food': koshari, 'grams': 10}, )
    services.add_item(user, meal, {'food': koshari, 'grams': 20})
    item = meal.items.first()
    other = make_user()
    for call in (lambda: services.update_meal(other, meal, note='x'),
                 lambda: services.delete_meal(other, meal),
                 lambda: services.add_item(other, meal, {'food': koshari, 'grams': 10}),
                 lambda: services.update_item(other, item, grams=20),
                 lambda: services.remove_item(other, item)):
        with pytest.raises(ValidationError):
            call()
    assert MealItem.objects.count() == 2


def test_cannot_log_another_users_custom_food(user, make_user):
    other = make_user()
    secret = services.create_custom_food(other, name_en='Secret', kcal=100, protein_g=1, carbs_g=1, fat_g=1)
    with pytest.raises(ValidationError) as exc:
        lunch(user, {'food': secret, 'grams': 100})
    assert 'Unknown food' in str(exc.value)


def test_can_log_own_custom_food(user):
    mine = services.create_custom_food(user, name_en='Mine', kcal=100, protein_g=1, carbs_g=1, fat_g=1)
    assert lunch(user, {'food': mine, 'grams': 100}).items.get().kcal == 100


def test_inactive_foods_cannot_be_logged(user, koshari):
    Food.objects.filter(pk=koshari.pk).update(is_active=False)
    with pytest.raises(ValidationError):
        lunch(user, {'food': koshari, 'grams': 100})


def test_deleting_a_custom_food_keeps_past_meals(user):
    mine = services.create_custom_food(user, name_en='Mine', kcal=100, protein_g=1, carbs_g=1, fat_g=1)
    item = lunch(user, {'food': mine, 'grams': 200}).items.get()
    services.delete_custom_food(user, mine)
    item.refresh_from_db()
    assert (item.food, item.name_en, item.kcal) == (None, 'Mine', Decimal(200))
