import csv
from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command

from apps.health import selectors, services
from apps.health.models import Food, FoodPortion

pytestmark = pytest.mark.django_db

HEADER = services.IMPORT_COLUMNS
KOSHARI = {'key': 'koshari', 'source': 'nni', 'category': 'dish', 'name_ar': 'كشري', 'name_en': 'Koshari',
           'name_fr': 'Koshari', 'aliases': 'كشرى|kushari', 'kcal': '150', 'protein_g': '5', 'carbs_g': '27',
           'fat_g': '2.5', 'portions': 'plate:350|small_plate:250', 'is_verified': 'true'}
EGG = {**KOSHARI, 'key': 'egg', 'category': 'protein', 'name_ar': 'بيض', 'name_en': 'Egg', 'name_fr': 'Œuf',
       'aliases': '', 'kcal': '155', 'protein_g': '13', 'carbs_g': '1.1', 'fat_g': '11', 'portions': 'piece:50'}


def write_csv(path, rows, header=HEADER):
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    return str(path)


def test_import_creates_foods_and_portions(tmp_path):
    call_command('import_foods', write_csv(tmp_path / 'f.csv', [KOSHARI, EGG]))
    koshari = Food.objects.get(key='koshari')
    assert (koshari.user, koshari.source, koshari.is_verified, koshari.kcal) == (None, 'nni', True, 150)
    assert koshari.aliases == ['كشرى', 'kushari']
    assert sorted((p.unit, p.grams) for p in koshari.portions.all()) == [('plate', 350), ('small_plate', 250)]


def test_import_is_idempotent(tmp_path, capsys):
    path = write_csv(tmp_path / 'f.csv', [KOSHARI, EGG])
    call_command('import_foods', path)
    call_command('import_foods', path)
    assert 'created 0, updated 0, unchanged 2' in capsys.readouterr().out
    assert Food.objects.count() == 2
    assert FoodPortion.objects.count() == 3


def test_reimport_updates_in_place(tmp_path, capsys):
    call_command('import_foods', write_csv(tmp_path / 'a.csv', [KOSHARI, EGG]))
    pk = Food.objects.get(key='koshari').pk
    changed = {**KOSHARI, 'kcal': '160', 'portions': 'plate:400|bowl:300'}
    call_command('import_foods', write_csv(tmp_path / 'b.csv', [changed, EGG]))
    assert 'created 0, updated 1, unchanged 1' in capsys.readouterr().out
    koshari = Food.objects.get(key='koshari')
    assert (koshari.pk, koshari.kcal) == (pk, 160)
    assert sorted((p.unit, p.grams) for p in koshari.portions.all()) == [('bowl', 300), ('plate', 400)]


def test_foods_missing_from_the_file_are_kept(tmp_path):
    call_command('import_foods', write_csv(tmp_path / 'a.csv', [KOSHARI, EGG]))
    call_command('import_foods', write_csv(tmp_path / 'b.csv', [KOSHARI]))
    assert Food.objects.count() == 2


def test_reimport_does_not_reactivate_a_hidden_food(tmp_path):
    path = write_csv(tmp_path / 'f.csv', [KOSHARI])
    call_command('import_foods', path)
    Food.objects.update(is_active=False)
    call_command('import_foods', path)
    assert Food.objects.get().is_active is False


def test_same_key_in_another_source_is_another_food(tmp_path):
    call_command('import_foods', write_csv(tmp_path / 'f.csv', [KOSHARI, {**KOSHARI, 'source': 'usda'}]))
    assert Food.objects.count() == 2


def test_user_custom_foods_are_never_matched(tmp_path, user):
    services.create_custom_food(user, name_en='Koshari', kcal=1, protein_g=0, carbs_g=0, fat_g=0)
    call_command('import_foods', write_csv(tmp_path / 'f.csv', [KOSHARI]))
    assert Food.objects.filter(user=user).get().kcal == 1


@pytest.mark.parametrize(('changes', 'message'), [
    ({'kcal': 'lots'}, 'kcal'),
    ({'kcal': '1000'}, 'kcal'),
    ({'key': ''}, 'Missing key'),
    ({'source': 'wiki'}, 'source'),
    ({'category': 'pizza'}, 'category'),
    ({'portions': 'plate'}, 'unit:grams'),
    ({'portions': 'spoon:10'}, 'unit'),
    ({'portions': 'plate:350|plate:250'}, 'once'),
    ({'is_verified': 'maybe'}, 'is_verified'),
    ({'name_ar': '', 'name_en': '', 'name_fr': ''}, 'name'),
])
def test_invalid_rows_import_nothing(tmp_path, changes, message):
    path = write_csv(tmp_path / 'f.csv', [EGG, {**KOSHARI, **changes}])
    with pytest.raises(CommandError) as exc:
        call_command('import_foods', path)
    assert 'line 3' in str(exc.value) and message in str(exc.value)
    assert not Food.objects.exists()


def test_duplicate_rows_are_refused(tmp_path):
    with pytest.raises(CommandError, match='Duplicate'):
        call_command('import_foods', write_csv(tmp_path / 'f.csv', [KOSHARI, KOSHARI]))
    assert not Food.objects.exists()


def test_unknown_columns_are_refused(tmp_path):
    with pytest.raises(CommandError, match='Unknown columns: colour'):
        call_command('import_foods', write_csv(tmp_path / 'f.csv', [{**KOSHARI, 'colour': 'x'}],
                                               header=[*HEADER, 'colour']))


def test_dry_run_writes_nothing(tmp_path, capsys):
    call_command('import_foods', write_csv(tmp_path / 'f.csv', [KOSHARI, EGG]), '--dry-run')
    assert 'would have created 2' in capsys.readouterr().out
    assert not Food.objects.exists()


def test_missing_file():
    with pytest.raises(CommandError, match='Cannot read'):
        call_command('import_foods', '/nonexistent/foods.csv')


# ----------------------------- seed ---------------------------------------- #
def test_seed_test_foods_is_idempotent_and_unverified(user):
    call_command('seed_test_foods')
    count = Food.objects.count()
    assert count >= 20
    call_command('seed_test_foods')
    assert Food.objects.count() == count
    assert not Food.objects.filter(is_verified=True).exists()
    assert not Food.objects.filter(portions__isnull=True).exists()  # every test food has a portion


def test_seeded_foods_are_searchable(user):
    call_command('seed_test_foods')
    assert [f.name_en for f in selectors.search_foods(user, 'طعميه')] == ['Falafel']
    assert [f.name_en for f in selectors.search_foods(user, 'فلافل')] == ['Falafel']
    koshari = Food.objects.get(key='test-koshari')
    assert koshari.portions.get(unit='plate').grams == Decimal(350)
