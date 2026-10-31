"""
Load ~20 TEST foods (apps/health/data/test_foods.csv), marked is_verified=False.
Approximate values for development and demos; the real dataset comes through
`import_foods`. Idempotent.

    python manage.py seed_test_foods
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from .import_foods import run_import

TEST_FOODS = Path(__file__).resolve().parents[2] / 'data' / 'test_foods.csv'


class Command(BaseCommand):
    help = 'Load the test foods (is_verified=False). Idempotent.'

    def handle(self, **options):
        result = run_import(TEST_FOODS)
        self.stdout.write(self.style.SUCCESS(
            f'Test foods: created {result.created}, updated {result.updated}, unchanged {result.unchanged}.'
        ))
