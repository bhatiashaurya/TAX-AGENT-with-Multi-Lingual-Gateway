"""Provider factory + router construction."""
from __future__ import annotations

from config.settings import settings
from providers.aws_provider import AWSProvider
from providers.azure_provider import AzureProvider
from providers.base import Provider
from providers.gcp_provider import GCPProvider
from providers.mock_provider import MockProvider
from providers.provider_router import ProviderRouter

_PROVIDER_CLASSES: dict[str, type[Provider]] = {
    "mock": MockProvider,
    "azure": AzureProvider,
    "gcp": GCPProvider,
    "aws": AWSProvider,
}


def build_provider(name: str) -> Provider:
    try:
        return _PROVIDER_CLASSES[name]()
    except KeyError as exc:
        raise KeyError(f"Unknown provider '{name}'") from exc


def build_router() -> ProviderRouter:
    """Register every provider (mock always; cloud providers whether or not they
    are configured -- an unconfigured provider simply reports unhealthy and the
    router falls back).  This lets the failover path be demonstrated offline."""
    providers = {name: cls() for name, cls in _PROVIDER_CLASSES.items()}
    return ProviderRouter(
        providers=providers,
        default=settings.DEFAULT_PROVIDER,
        fallback=settings.FALLBACK_PROVIDERS,
    )
