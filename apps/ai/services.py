"""
Writes of the ai app. Every provider call goes through call_provider(), which
records an AIUsage row for success AND failure (tokens, cost, latency).
"""

import time

from django.conf import settings

from . import config
from .models import AIUsage
from .providers import get_provider
from .providers.base import ProviderError, ProviderMisconfigured, StructuredRequest, StructuredResponse


def call_provider(*, user, cfg: config.FeatureConfig, request: StructuredRequest,
                  prompt_version: str) -> StructuredResponse:
    """
    One provider call with the feature's provider, model and effort, under the
    configured timeout. Always logs AIUsage; re-raises ProviderError on failure.
    """
    started = time.monotonic()
    usage = {'user': user, 'feature': cfg.feature, 'provider': cfg.provider, 'model': cfg.model,
             'prompt_version': prompt_version}
    try:
        if not config.provider_key_configured(cfg.provider):
            raise ProviderMisconfigured(f'No API key configured for {cfg.provider}.')
        response = get_provider(cfg.provider).structured(
            request, model=cfg.model, effort=cfg.effort, timeout=settings.AI_TIMEOUT_SECONDS,
        )
    except ProviderError as exc:
        _record(usage, started, exc.input_tokens, exc.output_tokens, success=False, error_code=exc.code)
        raise
    except Exception:
        _record(usage, started, 0, 0, success=False, error_code='internal_error')
        raise
    _record({**usage, 'model': response.model or cfg.model}, started, response.input_tokens,
            response.output_tokens, success=True)
    return response


def _record(usage: dict, started: float, input_tokens: int, output_tokens: int, *, success: bool,
            error_code: str = '') -> AIUsage:
    return AIUsage.objects.create(
        **usage, input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=config.estimate_cost(usage['provider'], usage['model'], input_tokens, output_tokens),
        latency_ms=int((time.monotonic() - started) * 1000), success=success, error_code=error_code,
    )
