"""
The provider-agnostic interface. A provider turns a request (system prompt, the
user's input as data, a JSON schema; later images) into JSON matching the schema.
It never calls tools and never writes anything: the caller validates the JSON and
decides what to do with it.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class StructuredRequest:
    system: str                 # fixed instructions (operator)
    user_content: str           # the user's input, wrapped as data
    schema: dict                # JSON schema the output must match
    images: tuple = field(default_factory=tuple)  # later rounds (meal_photo)
    max_output_tokens: int = 4000


@dataclass(frozen=True)
class StructuredResponse:
    data: dict                  # parsed JSON (not yet validated against our rules)
    input_tokens: int
    output_tokens: int
    model: str                  # the model that actually answered


class ProviderError(Exception):
    """A provider call failed. `code` is stored on AIUsage and on the draft."""

    code = 'provider_error'

    def __init__(self, message: str = '', *, input_tokens: int = 0, output_tokens: int = 0):
        super().__init__(message or self.code)
        self.input_tokens = input_tokens    # tokens billed even though the call failed
        self.output_tokens = output_tokens


class ProviderTimeout(ProviderError):
    code = 'timeout'


class ProviderUnavailable(ProviderError):
    code = 'provider_unavailable'       # rate limited, 5xx, network


class ProviderMisconfigured(ProviderError):
    code = 'provider_misconfigured'     # missing/invalid API key, unknown model


class ProviderRefused(ProviderError):
    code = 'refused'                    # the model declined (safety)


class ProviderBadOutput(ProviderError):
    code = 'invalid_output'             # not JSON, or truncated


class Provider(Protocol):
    name: str

    def structured(self, request: StructuredRequest, *, model: str, effort: str,
                   timeout: float) -> StructuredResponse: ...
