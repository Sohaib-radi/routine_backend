"""Provider registry: the configured provider name -> its adapter."""

from .base import ProviderError  # noqa: F401


def get_provider(name: str):
    if name == 'fake':
        from .fake import FakeProvider

        return FakeProvider()
    if name == 'anthropic':
        from .anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    from .base import ProviderMisconfigured

    raise ProviderMisconfigured(f'Unknown AI provider “{name}”.')
