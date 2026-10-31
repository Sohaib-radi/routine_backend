"""Initial USD prices per million tokens (Anthropic first-party rates, 2026-10). Editable in the admin."""

from decimal import Decimal

from django.db import migrations

PRICES = [
    ('anthropic', 'claude-haiku-5-5', Decimal('0.10'), Decimal('0.50')),
    ('anthropic', 'claude-sonnet-5-5', Decimal('2.00'), Decimal('10.00')),
    ('anthropic', 'claude-opus-5-5', Decimal('4.00'), Decimal('20.00')),
    ('fake', 'fake-model', Decimal('0'), Decimal('0')),
]


def seed(apps, schema_editor):
    ProviderPrice = apps.get_model('ai', 'ProviderPrice')
    for provider, model, input_price, output_price in PRICES:
        ProviderPrice.objects.get_or_create(provider=provider, model=model, defaults={
            'input_usd_per_mtok': input_price, 'output_usd_per_mtok': output_price})


class Migration(migrations.Migration):
    dependencies = [('ai', '0001_initial')]
    operations = [migrations.RunPython(seed, migrations.RunPython.noop)]
