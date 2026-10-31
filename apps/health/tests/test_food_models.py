from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.urls import reverse

from apps.health.models import PORTION_UNIT_LABELS, Food, FoodPortion, PortionUnit

pytestmark = pytest.mark.django_db


def make_food(**fields):
    defaults = {'key': 'tameya', 'source': Food.Source.NNI, 'name_ar': 'طعمية', 'name_en': 'Falafel',
                'name_fr': 'Falafel', 'aliases': ['فلافل', "ta'meya"], 'category': Food.Category.DISH,
                'kcal': Decimal(333), 'protein_g': Decimal('13.3'), 'carbs_g': Decimal('31.8'),
                'fat_g': Decimal('17.8')}
    return Food.objects.create(**{**defaults, **fields})


def test_search_text_holds_normalized_names_and_aliases():
    food = make_food()
    assert food.search_text == 'طعميه | falafel | falafel | فلافل | tameya'


def test_search_text_follows_edits():
    food = make_food()
    food.aliases = ['بطاطس']
    food.save(update_fields=['aliases'])
    food.refresh_from_db()
    assert 'بطاطس' in food.search_text
    assert 'tameya' not in food.search_text


def test_a_name_is_required():
    with pytest.raises(ValidationError) as exc:
        Food(key='x', name_ar='', name_en='', name_fr='', kcal=1, protein_g=0, carbs_g=0, fat_g=0).full_clean()
    assert 'name_en' in exc.value.message_dict


def test_macros_cannot_exceed_100g():
    with pytest.raises(ValidationError) as exc:
        Food(key='x', name_en='Bad', kcal=100, protein_g=50, carbs_g=40, fat_g=11).full_clean()
    assert 'fat_g' in exc.value.message_dict


def test_global_food_needs_a_key():
    with pytest.raises(ValidationError) as exc:
        Food(name_en='No key', kcal=1, protein_g=0, carbs_g=0, fat_g=0).full_clean()
    assert 'key' in exc.value.message_dict


def test_global_key_is_unique_per_source():
    make_food()
    make_food(source=Food.Source.USDA)  # same key, other source: fine
    with pytest.raises(IntegrityError):
        make_food()


def test_custom_foods_must_have_custom_source(user):
    with pytest.raises(IntegrityError):
        make_food(user=user, source=Food.Source.NNI)


def test_every_unit_has_three_labels():
    assert set(PORTION_UNIT_LABELS) == set(PortionUnit.values)
    for labels in PORTION_UNIT_LABELS.values():
        assert set(labels) == {'ar', 'en', 'fr'} and all(labels.values())


def test_one_portion_per_unit():
    food = make_food()
    FoodPortion.objects.create(food=food, unit=PortionUnit.PIECE, grams=30)
    with pytest.raises(IntegrityError):
        FoodPortion.objects.create(food=food, unit=PortionUnit.PIECE, grams=40)


# ----------------------------- admin --------------------------------------- #
@pytest.fixture
def admin_client(client):
    from apps.accounts.models import User

    client.force_login(User.objects.create_superuser('admin@example.com', 'S3cure-pass!'))
    return client


def test_staff_edit_global_foods(admin_client):
    food = make_food()
    assert admin_client.get(reverse('admin:health_food_changelist')).status_code == 200
    assert admin_client.get(reverse('admin:health_food_add')).status_code == 200
    r = admin_client.get(reverse('admin:health_food_change', args=[food.pk]))
    assert 'kcal' in r.context['adminform'].form.fields


def test_custom_foods_are_read_only_in_admin(admin_client, user):
    food = make_food(user=user, source=Food.Source.CUSTOM, key='')
    r = admin_client.get(reverse('admin:health_food_change', args=[food.pk]))
    assert r.status_code == 200
    assert 'kcal' not in r.context['adminform'].form.fields
    assert admin_client.post(reverse('admin:health_food_delete', args=[food.pk]), {'post': 'yes'}).status_code == 403
