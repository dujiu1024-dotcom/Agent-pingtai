"""Extension package validation and lifecycle public interfaces."""
from agent_platform.extensions.manager import (
    ExtensionLifecycleManager, InMemoryExtensionStateStore, InstalledExtension,
)
from agent_platform.extensions.manifest import ExtensionManifest, validate_platform_compatibility

__all__ = [
    "ExtensionLifecycleManager", "InMemoryExtensionStateStore",
    "InstalledExtension", "ExtensionManifest", "validate_platform_compatibility",
]
