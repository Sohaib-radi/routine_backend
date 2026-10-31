"""
Anthropic (Claude) adapter, through the official `anthropic` SDK.

- Structured outputs: `output_config.format` with our JSON schema, so the reply is
  JSON matching it (we still validate our own rules afterwards).
- No tools are declared: the model can only answer.
- `effort` controls thinking depth (thinking stays adaptive; Haiku 5.5 / Opus 5.5
  cannot turn it off).
- Refusal fallback: on models that support it, the server-side `fallbacks: "default"`
  option re-runs a safety-declined request on a fallback model inside the same call.
- The API key comes from the ANTHROPIC_API_KEY environment variable (read by the SDK).
"""

import json

from django.conf import settings

from .base import (
    ProviderBadOutput,
    ProviderError,
    ProviderMisconfigured,
    ProviderRefused,
    ProviderTimeout,
    ProviderUnavailable,
    StructuredRequest,
    StructuredResponse,
)

# Models that accept the server-side refusal fallback (`fallbacks: "default"`).
FALLBACK_MODELS = {'claude-fable-5-1', 'claude-opus-5-5', 'claude-opus-5', 'claude-sonnet-5-5'}
FALLBACK_BETA = 'server-side-fallback-2026-07-01'


class AnthropicProvider:
    name = 'anthropic'

    def __init__(self, client=None):
        self._client = client  # injected in tests; built lazily otherwise

    def _get_client(self, timeout: float):
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(timeout=timeout, max_retries=settings.AI_PROVIDER_MAX_RETRIES)
        return self._client

    def structured(self, request: StructuredRequest, *, model: str, effort: str,
                   timeout: float) -> StructuredResponse:
        import anthropic

        params = {
            'model': model,
            'max_tokens': request.max_output_tokens,
            'system': request.system,
            'messages': [{'role': 'user', 'content': self._content(request)}],
            'output_config': {'effort': effort, 'format': {'type': 'json_schema', 'schema': request.schema}},
        }
        client = self._get_client(timeout)
        try:
            if model in FALLBACK_MODELS:
                response = client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks='default', **params)
            else:
                response = client.messages.create(**params)
        except anthropic.APITimeoutError as exc:
            raise ProviderTimeout(str(exc)) from exc
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError, anthropic.NotFoundError) as exc:
            raise ProviderMisconfigured(str(exc)) from exc
        except anthropic.BadRequestError as exc:
            raise ProviderError(str(exc)) from exc
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            raise ProviderUnavailable(str(exc)) from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise ProviderUnavailable(str(exc)) from exc
            raise ProviderError(str(exc)) from exc

        tokens = {'input_tokens': response.usage.input_tokens, 'output_tokens': response.usage.output_tokens}
        if response.stop_reason == 'refusal':
            category = getattr(response.stop_details, 'category', None) if response.stop_details else None
            raise ProviderRefused(f'Declined ({category or "unspecified"}).', **tokens)
        if response.stop_reason == 'max_tokens':
            raise ProviderBadOutput('Output truncated (max_tokens).', **tokens)
        text = next((block.text for block in response.content if block.type == 'text'), None)
        try:
            data = json.loads(text) if text is not None else None
        except json.JSONDecodeError:
            data = None
        if not isinstance(data, dict):
            raise ProviderBadOutput('No JSON object in the reply.', **tokens)
        return StructuredResponse(data=data, model=response.model, **tokens)

    @staticmethod
    def _content(request: StructuredRequest):
        if not request.images:
            return request.user_content
        # Later rounds (meal_photo): image blocks first, then the text.
        return [*request.images, {'type': 'text', 'text': request.user_content}]
