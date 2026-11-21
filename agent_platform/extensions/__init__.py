"""Extension package validation and lifecycle public interfaces."""
from agent_platform.extensions.manager import (
    ExtensionLifecycleManager,
    InMemoryExtensionStateStore,
    InstalledExtension,
)
from agent_platform.extensions.manifest import ExtensionManifest, validate_platform_compatibility
from agent_platform.extensions.postgres import (
    PostgresExtensionStateStore,
    PostgresTrustedPublisherKeyRepository,
)
from agent_platform.extensions.publishers import TrustedPublisherKeyRegistry

__all__ = [
    "ExtensionLifecycleManager", "InMemoryExtensionStateStore",
    "InstalledExtension", "ExtensionManifest", "validate_platform_compatibility",
    "TrustedPublisherKeyRegistry", "PostgresExtensionStateStore",
    "PostgresTrustedPublisherKeyRepository",
]
