"""
Fake provider for tests and local development: replays scripted responses and
records the requests it received. It never touches the network.

    from apps.ai.providers import fake
    fake.script({'meal_type': None, 'items': [...]}, ProviderTimeout())
"""

from collections import deque

from .base import ProviderBadOutput, ProviderError, StructuredRequest, StructuredResponse

INPUT_TOKENS = 120
OUTPUT_TOKENS = 60

_queue: deque = deque()
calls: list[dict] = []


def script(*responses) -> None:
    """Queue responses: a dict is returned as the JSON, a ProviderError is raised, a str is invalid JSON."""
    _queue.extend(responses)


def reset() -> None:
    _queue.clear()
    calls.clear()


class FakeProvider:
    name = 'fake'

    def structured(self, request: StructuredRequest, *, model: str, effort: str,
                   timeout: float) -> StructuredResponse:
        calls.append({'request': request, 'model': model, 'effort': effort, 'timeout': timeout})
        response = _queue.popleft() if _queue else {'meal_type': None, 'items': []}
        if isinstance(response, ProviderError):
            raise response
        if isinstance(response, str):
            raise ProviderBadOutput('Not JSON.', input_tokens=INPUT_TOKENS, output_tokens=OUTPUT_TOKENS)
        return StructuredResponse(data=response, input_tokens=INPUT_TOKENS, output_tokens=OUTPUT_TOKENS,
                                  model=model)
