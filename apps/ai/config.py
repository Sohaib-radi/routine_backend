"""
Effective AI configuration, read on every call without touching the database:
the AIFeatureConfig row (cached in Redis, invalidated on save) or, without a row,
the environment defaults (settings.AI_DEFAULTS). settings.AI_FORCE_PROVIDER (tests)
overrides the provider whatever the row says.

API keys are never stored: only whether the provider's environment variable is set.
"""

import os
from dataclasses import asdict, dataclass
from decimal import Decimal

from django.conf import settings
from django.core.cache import cache

CACHE_TTL = 24 * 60 * 60  # invalidated on save anyway; the TTL only bounds staleness if a signal is missed
_MISSING = '__missing__'


@dataclass(frozen=True)
class FeatureConfig:
    feature: str
    enabled: bool
    provider: str
    model: str
    effort: str
    daily_limit: int
    source: str  # 'admin' (a config row) or 'environment' (settings.AI_DEFAULTS)


def _feature_key(feature: str) -> str:
    return f'ai:feature-config:{feature}'


def _price_key(provider: str, model: str) -> str:
    return f'ai:price:{provider}:{model}'


def _defaults(feature: str) -> dict:
    d = settings.AI_DEFAULTS
    return {'feature': feature, 'enabled': d['enabled'], 'provider': d['provider'], 'model': d['model'],
            'effort': d['effort'], 'daily_limit': d['daily_limit'], 'source': 'environment'}


def feature_config(feature: str) -> FeatureConfig:
    from .models import AIFeatureConfig

    values = cache.get(_feature_key(feature))
    if values is None:
        row = AIFeatureConfig.objects.filter(feature=feature).first()
        values = _defaults(feature) if row is None else {
            'feature': feature, 'enabled': row.enabled, 'provider': row.provider, 'model': row.model,
            'effort': row.effort, 'daily_limit': row.daily_limit, 'source': 'admin',
        }
        cache.set(_feature_key(feature), values, CACHE_TTL)
    if settings.AI_FORCE_PROVIDER:
        values = {**values, 'provider': settings.AI_FORCE_PROVIDER}
    return FeatureConfig(**values)


def invalidate_feature(feature: str) -> None:
    cache.delete(_feature_key(feature))


def price_per_mtok(provider: str, model: str) -> tuple[Decimal, Decimal] | None:
    """(input, output) USD per million tokens, or None when no price is configured."""
    from .models import ProviderPrice

    value = cache.get(_price_key(provider, model))
    if value is None:
        row = ProviderPrice.objects.filter(provider=provider, model=model).first()
        value = _MISSING if row is None else (str(row.input_usd_per_mtok), str(row.output_usd_per_mtok))
        cache.set(_price_key(provider, model), value, CACHE_TTL)
    if value == _MISSING:
        return None
    return Decimal(value[0]), Decimal(value[1])


def invalidate_price(provider: str, model: str) -> None:
    cache.delete(_price_key(provider, model))


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> Decimal | None:
    price = price_per_mtok(provider, model)
    if price is None:
        return None
    cost = (Decimal(input_tokens) * price[0] + Decimal(output_tokens) * price[1]) / Decimal(1_000_000)
    return cost.quantize(Decimal('0.000001'))


def provider_key_configured(provider: str) -> bool:
    """Whether the provider's API key environment variable is set. Never returns the key."""
    variable = settings.AI_PROVIDER_KEY_ENV.get(provider)
    if variable is None:  # the fake provider needs no key
        return True
    return bool(os.environ.get(variable))


def as_dict(cfg: FeatureConfig) -> dict:
    return asdict(cfg)
