"""try_meal_text, with the fake provider (tests never call a real one)."""

import pytest
from django.core.management import call_command

from apps.ai.models import AIUsage, MealDraft
from apps.ai.providers import fake
from apps.ai.providers.base import ProviderTimeout
from apps.health.models import Food, Meal

from .conftest import extraction, item

pytestmark = pytest.mark.django_db


def test_try_meal_text_prints_matches_and_saves_nothing(capsys):
    Food.objects.create(key='ful', source='nni', name_ar='فول مدمس', name_en='Ful', kcal=110, protein_g=7,
                        carbs_g=15, fat_g=2)
    fake.script(extraction(item('فول', 1, 'sandwich', estimated_grams=150), item('سوشي'), meal_type='breakfast'),
                ProviderTimeout(), 'not json')
    call_command('try_meal_text', 'فطرت فول وسوشي', 'slow', 'broken')
    out = capsys.readouterr().out
    assert 'model claude-haiku-5-5' in out
    assert '✓ فول [1 sandwich] → فول مدمس · 150.0 g (estimated) · 165.0 kcal' in out
    assert '~ سوشي [1] → no match, AI estimate 100.0 g · 100.0 kcal' in out
    assert 'timeout' in out and 'invalid output' in out
    assert not MealDraft.objects.exists() and not Meal.objects.exists()
    assert AIUsage.objects.filter(user__isnull=True).count() == 3


def test_try_meal_text_runs_the_samples_with_overrides(capsys):
    call_command('try_meal_text', '--model', 'claude-opus-5-5', '--effort', 'medium')
    out = capsys.readouterr().out
    assert 'model claude-opus-5-5, effort medium' in out
    assert out.count('no food found') == 8  # the fake answers "no items" to every sample
    assert fake.calls[0]['model'] == 'claude-opus-5-5'
