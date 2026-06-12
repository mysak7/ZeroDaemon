"""Provider registry — maps a provider name to its WorkerProvider class.

To add a cloud: implement WorkerProvider in a new module here and register it
in _PROVIDER_CLASSES. Nothing else in the codebase needs to change.
"""

from __future__ import annotations

from typing import Optional, Type

from zerodaemon.workers.base import WorkerProvider
from zerodaemon.workers.providers.gcp import GcpProvider

_PROVIDER_CLASSES: dict[str, Type[WorkerProvider]] = {
    "gcp": GcpProvider,
    # "aws": AwsProvider,     # future
    # "azure": AzureProvider, # future
}


def known_providers() -> list[str]:
    return list(_PROVIDER_CLASSES.keys())


def build_provider(name: str, config: dict) -> Optional[WorkerProvider]:
    cls = _PROVIDER_CLASSES.get(name)
    if cls is None:
        return None
    return cls(config)
