"""Food search and custom foods (selectors + services)."""

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.health import selectors, services
from apps.health.models import Food, FoodPortion

pytestmark = pytest.mark.django_db


def food(key, name_ar='', name_en='', name_fr='', aliases=(), **fields):
    defaults = {'kcal': 100, 'protein_g': 1, 'carbs_g': 1, 'fat_g': 1, 'source': Food.Source.NNI}
    return Food.objects.create(key=key, name_ar=name_ar, name_en=name_en, name_fr=name_fr, aliases=list(aliases),
                               **{**defaults, **fields})


@pytest.fixture
def foods():
    return {
        'tameya': food('tameya', 'طعمية', 'Falafel', 'Falafel', ['فلافل', "ta'meya"]),
        'koshari': food('koshari', 'كشري', 'Koshari', 'Kochari'),
        'rice': food('rice', 'أرز أبيض', 'White rice', 'Riz blanc'),
        'pasta': food('pasta', 'مكرونة', 'Pasta', 'Pâtes'),
        'tea': food('tea', 'شاي بسكر', 'Tea with sugar', 'Thé au sucre'),
    }


def keys(results):
    return [f.key for f in results]


# ----------------------------- search -------------------------------------- #
@pytest.mark.parametrize('query', ['طعمية', 'طعميه', 'طَعْمِيَّة', 'فلافل', 'falafel', 'FALAFEL', "ta'meya", 'tameya'])
def test_search_finds_a_food_by_any_name_or_alias(user, foods, query):
    assert keys(selectors.search_foods(user, query)) == ['tameya']


def test_arabic_hamza_normalization(user, foods):
    assert keys(selectors.search_foods(user, 'ارز')) == ['rice']
    assert keys(selectors.search_foods(user, 'أرز')) == ['rice']


def test_search_in_english_and_french(user, foods):
    assert keys(selectors.search_foods(user, 'rice')) == ['rice']
    assert keys(selectors.search_foods(user, 'riz')) == ['rice']
    assert keys(selectors.search_foods(user, 'pates')) == ['pasta']
    assert keys(selectors.search_foods(user, 'thé')) == ['tea']


def test_search_matches_inside_names(user, foods):
    assert keys(selectors.search_foods(user, 'sugar')) == ['tea']


def test_exact_and_prefix_matches_come_first(user):
    food('rice-pudding', name_en='Rice pudding')
    food('fried-rice', name_en='Fried rice')
    food('rice', name_en='Rice')
    assert keys(selectors.search_foods(user, 'rice')) == ['rice', 'rice-pudding', 'fried-rice']


def test_short_queries_return_nothing(user, foods):
    assert selectors.search_foods(user, 'ك') == []
    assert selectors.search_foods(user, '  ') == []


def test_limit(user):
    for i in range(30):
        food(f'bread-{i}', name_en=f'Bread {i}')
    assert len(selectors.search_foods(user, 'bread')) == 20
    assert len(selectors.search_foods(user, 'bread', limit=5)) == 5
    assert len(selectors.search_foods(user, 'bread', limit=500)) == 30  # capped at 50


def test_inactive_foods_are_hidden(user, foods):
    Food.objects.filter(key='koshari').update(is_active=False)
    assert selectors.search_foods(user, 'koshari') == []


def test_like_wildcards_are_literal(user, foods):
    assert selectors.search_foods(user, '%%') == []
    assert selectors.search_foods(user, '__') == []


def test_search_uses_the_trigram_index(user, foods):
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute('SET enable_seqscan = off')
        cursor.execute("EXPLAIN SELECT id FROM health_food WHERE search_text LIKE '%%falafel%%'")
        plan = ' '.join(row[0] for row in cursor.fetchall())
        cursor.execute('SET enable_seqscan = on')
    assert 'food_search_trgm' in plan


# ----------------------------- custom foods -------------------------------- #
FIELDS = {'name_ar': 'كيكة ماما', 'name_en': "Mom's cake", 'category': 'sweet', 'kcal': Decimal(380),
          'protein_g': Decimal(5), 'carbs_g': Decimal(50), 'fat_g': Decimal(18)}


def test_create_custom_food(user):
    f = services.create_custom_food(user, portions=[{'unit': 'slice', 'grams': 80}], **FIELDS)
    assert (f.user, f.source, f.is_verified, f.key) == (user, 'custom', False, '')
    assert [(p.unit, p.grams) for p in f.portions.all()] == [('slice', Decimal(80))]


def test_custom_food_is_found_by_its_owner_only(user, make_user):
    services.create_custom_food(user, **FIELDS)
    assert len(selectors.search_foods(user, 'كيكه')) == 1
    other = make_user()
    assert selectors.search_foods(other, 'كيكه') == []
    assert not selectors.foods_for(other).filter(user=user).exists()


@pytest.mark.parametrize(('changes', 'field'), [
    ({'kcal': Decimal(901)}, 'kcal'),
    ({'protein_g': Decimal(-1)}, 'protein_g'),
    ({'protein_g': Decimal(60), 'carbs_g': Decimal(50)}, 'fat_g'),  # > 100 g of macros
    ({'name_ar': '', 'name_en': ''}, 'name_en'),
])
def test_custom_food_validation(user, changes, field):
    with pytest.raises(ValidationError) as exc:
        services.create_custom_food(user, **{**FIELDS, **changes})
    assert field in exc.value.message_dict
    assert not Food.objects.exists()


@pytest.mark.parametrize('portions', [
    [{'unit': 'spoonful', 'grams': 10}],
    [{'unit': 'slice', 'grams': 0}],
    [{'unit': 'slice', 'grams': 5001}],
    [{'unit': 'slice', 'grams': 80}, {'unit': 'slice', 'grams': 90}],
])
def test_custom_food_portion_validation(user, portions):
    with pytest.raises(ValidationError) as exc:
        services.create_custom_food(user, portions=portions, **FIELDS)
    assert 'portions' in exc.value.message_dict
    assert not Food.objects.exists()


def test_unknown_fields_are_refused(user):
    with pytest.raises(ValidationError):
        services.create_custom_food(user, is_verified=True, **FIELDS)


def test_update_custom_food(user):
    f = services.create_custom_food(user, portions=[{'unit': 'slice', 'grams': 80}], **FIELDS)
    f = services.update_custom_food(user, f, kcal=Decimal(350), portions=[{'unit': 'piece', 'grams': 60}])
    assert f.kcal == 350
    assert [p.unit for p in f.portions.all()] == ['piece']


def test_update_without_portions_keeps_them(user):
    f = services.create_custom_food(user, portions=[{'unit': 'slice', 'grams': 80}], **FIELDS)
    services.update_custom_food(user, f, name_en='Cake')
    assert FoodPortion.objects.filter(food=f).count() == 1


def test_cannot_edit_or_delete_global_foods(user, foods):
    with pytest.raises(ValidationError):
        services.update_custom_food(user, foods['koshari'], kcal=1)
    with pytest.raises(ValidationError):
        services.delete_custom_food(user, foods['koshari'])
    assert Food.objects.get(key='koshari').kcal == 100


def test_cannot_edit_or_delete_another_users_food(user, make_user):
    f = services.create_custom_food(user, **FIELDS)
    other = make_user()
    with pytest.raises(ValidationError):
        services.update_custom_food(other, f, kcal=1)
    with pytest.raises(ValidationError):
        services.delete_custom_food(other, f)
    assert Food.objects.filter(pk=f.pk).exists()


def test_delete_custom_food(user):
    f = services.create_custom_food(user, **FIELDS)
    services.delete_custom_food(user, f)
    assert not Food.objects.exists()


# ----------------------------- AI source ----------------------------------- #
def test_create_ai_food(user):
    f = services.create_custom_food(user, source='ai', **FIELDS)
    assert (f.source, f.is_verified, f.user) == ('ai', False, user)
    assert [x.pk for x in selectors.search_foods(user, 'كيكه')] == [f.pk]  # found by its owner


def test_private_foods_cannot_use_a_dataset_source(user):
    with pytest.raises(ValidationError):
        services.create_custom_food(user, source='nni', **FIELDS)


def test_ai_foods_can_be_edited_and_deleted_by_their_owner(user):
    f = services.create_custom_food(user, source='ai', **FIELDS)
    assert services.update_custom_food(user, f, kcal=Decimal(300)).kcal == 300
    services.delete_custom_food(user, f)
    assert not Food.objects.exists()


# ----------------------------- match_foods (AI names) ---------------------- #
@pytest.fixture
def menu():
    return {
        'ful': food('ful', 'فول مدمس', 'Ful medames', 'Foul medames', ['فول']),
        'peanuts': food('peanuts', 'فول سوداني', 'Roasted peanuts', 'Cacahuètes'),
        'tameya': food('tameya', 'طعمية', 'Falafel', 'Falafel', ['فلافل']),
        'tea': food('tea', 'شاي بسكر', 'Tea with sugar', 'Thé au sucre', ['شاي']),
    }


def test_match_uses_the_normal_search_first(user, menu):
    assert keys(selectors.match_foods(user, 'طعميه')) == ['tameya']


def test_match_finds_a_food_phrased_differently(user, menu):
    found = keys(selectors.match_foods(user, 'فول مدمس بالزيت'))
    assert found[0] == 'ful'


def test_match_returns_best_plus_alternatives(user, menu):
    found = keys(selectors.match_foods(user, 'فول'))
    assert found[0] == 'ful' and 'peanuts' in found and len(found) <= 4


def test_match_in_english_with_extra_words(user, menu):
    assert keys(selectors.match_foods(user, 'a cup of tea with sugar'))[0] == 'tea'


def test_no_match_for_unknown_food(user, menu):
    assert selectors.match_foods(user, 'sushi platter') == []
    assert selectors.match_foods(user, 'ab') == []


def test_match_includes_own_private_foods_only(user, make_user, menu):
    services.create_custom_food(make_user(), source='ai', name_ar='كيكة', kcal=1, protein_g=0, carbs_g=0, fat_g=0)
    assert selectors.match_foods(user, 'كيكة بالشوكولاتة') == []
