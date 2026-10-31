"""
Load global foods from a CSV file (format: docs/health.md, "Food CSV format").

    python manage.py import_foods foods.csv [--dry-run]

Idempotent: foods are matched on (source, key); running it twice updates, never
duplicates. All or nothing: one invalid row and nothing is written.
"""

import csv

from django.core.management.base import BaseCommand, CommandError

from apps.health import services


def run_import(path, *, dry_run=False, stdout=None):
    try:
        with open(path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            unknown = set(reader.fieldnames or []) - set(services.IMPORT_COLUMNS)
            if unknown:
                raise CommandError(f'Unknown columns: {", ".join(sorted(unknown))}.')
            result = services.import_foods(reader, dry_run=dry_run)
    except OSError as exc:
        raise CommandError(f'Cannot read {path}: {exc}') from None
    if result.errors:
        raise CommandError('Nothing imported:\n' + '\n'.join(result.errors))
    return result


class Command(BaseCommand):
    help = 'Create or update global foods from a CSV file (idempotent).'

    def add_arguments(self, parser):
        parser.add_argument('path')
        parser.add_argument('--dry-run', action='store_true', help='Validate and count, write nothing.')

    def handle(self, path, dry_run, **options):
        result = run_import(path, dry_run=dry_run)
        prefix = 'Dry run: would have ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(
            f'{prefix}created {result.created}, updated {result.updated}, unchanged {result.unchanged}.'
        ))
