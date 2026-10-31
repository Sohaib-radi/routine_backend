import pytest
from django.core.cache import cache

from apps.ai.providers import fake


@pytest.fixture(autouse=True)
def _clean():
    fake.reset()
    cache.clear()
    yield
    fake.reset()
    cache.clear()


def item(name, quantity=1, unit=None, grams=None, estimated_grams=100, kcal=100, protein_g=5, carbs_g=10,
         fat_g=3):
    """One item as the model returns it."""
    return {'name': name, 'quantity': quantity, 'unit': unit, 'grams': grams, 'estimated_grams': estimated_grams,
            'estimated_per_100g': {'kcal': kcal, 'protein_g': protein_g, 'carbs_g': carbs_g, 'fat_g': fat_g}}


def extraction(*items, meal_type=None):
    return {'meal_type': meal_type, 'items': list(items)}
