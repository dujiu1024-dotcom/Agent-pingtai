"""Authorized extension lifecycle orchestration with dependency locks and compensation."""
from __future__ import annotations

import asyncio
import inspect
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from agent_platform.extensions.lifecycle import ExtensionLifecycle
from agent_platform.extensions.manifest import ExtensionManifest, validate_platform_compatibility


@dataclass(frozen=True)
class InstalledExtension:
    manifest: ExtensionManifest
    status: str = "disabled"
    artifact: Any = None

    def __post_init__(self) -> None:
        if self.status not in {"disabled", "enabled"}:
            raise ValueError("extension status must be disabled or enabled")


class ExtensionStateStore(Protocol):
    async def list(self) -> Mapping[str, InstalledExtension]: ...
    async def get(self, extension_id: str) -> InstalledExtension | None: ...
    async def put(self, item: InstalledExtension) -> None: ...
    async def remove(self, extension_id: str) -> None: ...


class ExtensionRuntime(Protocol):
    async def install(self, manifest: ExtensionManifest, artifact: Any) -> None: ...
    async def enable(self, manifest: ExtensionManifest) -> None: ...
    async def disable(self, manifest: ExtensionManifest) -> None: ...
    async def upgrade(self, previous: ExtensionManifest, replacement: ExtensionManifest,
                      artifact: Any) -> None: ...
    async def uninstall(self, manifest: ExtensionManifest) -> None: ...


async def _call(callback: Callable[..., Any], *args: Any) -> Any:
    value = callback(*args)
    return await value if inspect.isawaitable(value) else value


class InMemoryExtensionStateStore:
    """Test/development registry. Production must provide durable transactional storage."""

    def __init__(self) -> None:
        self._items: dict[str, InstalledExtension] = {}

    async def list(self) -> Mapping[str, InstalledExtension]:
        return dict(self._items)

    async def get(self, extension_id: str) -> InstalledExtension | None:
        return self._items.get(extension_id)

    async def put(self, item: InstalledExtension) -> None:
        self._items[item.manifest.extension_id] = item

    async def remove(self, extension_id: str) -> None:
        self._items.pop(extension_id, None)


class ExtensionLifecycleManager:
    """Manage install/enable/upgrade/disable/uninstall with permission and audit gates."""

    def __init__(self, *, store: ExtensionStateStore, runtime: ExtensionRuntime,
                 platform_api_version: str,
                 authorize: Callable[[str, ExtensionManifest, str], Any],
                 audit: Callable[[str, ExtensionManifest, str], Any]) -> None:
        if not callable(authorize) or not callable(audit):
            raise TypeError("extension authorization and audit callbacks are required")
        self.store = store
        self.runtime = runtime
        self.lifecycle = ExtensionLifecycle(platform_api_version=platform_api_version)
        self.authorize = authorize
        self.audit = audit
        self._lock = asyncio.Lock()

    async def _authorize(self, action: str, manifest: ExtensionManifest) -> None:
        for permission in manifest.permissions:
            if await _call(self.authorize, action, manifest, permission) is not True:
                raise PermissionError(
                    f"extension {manifest.extension_id} is not authorized for {permission}"
                )

    async def _audit(self, action: str, manifest: ExtensionManifest, outcome: str) -> None:
        # Audit failures propagate and prevent the following side effect.
        await _call(self.audit, action, manifest, outcome)

    async def install(self, manifest: ExtensionManifest, *, artifact: Any,
                      available: Mapping[str, ExtensionManifest] | None = None,
                      dependency_artifacts: Mapping[str, Any] | None = None) -> tuple[str, ...]:
        async with self._lock:
            installed = await self.store.list()
            installed_manifests = {key: value.manifest for key, value in installed.items()}
            plan = self.lifecycle.plan_install(
                manifest, installed=installed_manifests, available=available or {},
            )
            package_artifacts = dict(dependency_artifacts or {})
            package_artifacts[manifest.extension_id] = artifact
            if any(not package_artifacts.get(item.extension_id) for item in plan.manifests):
                raise ValueError("verified artifacts are required for every new dependency")
            for item in plan.manifests:
                await self._authorize("install", item)
            completed: list[ExtensionManifest] = []
            try:
                for item in plan.manifests:
                    await self._audit("install", item, "requested")
                    package = package_artifacts[item.extension_id]
                    await self.runtime.install(item, package)
                    completed.append(item)
                    await self.store.put(InstalledExtension(item, "disabled", package))
                    await self._audit("install", item, "succeeded")
            except Exception:
                for item in reversed(completed):
                    try:
                        await self._audit("rollback_install", item, "requested")
                        await self.runtime.uninstall(item)
                        await self.store.remove(item.extension_id)
                        await self._audit("rollback_install", item, "succeeded")
                    except Exception:
                        # Preserve the original failure; reconciliation can inspect durable audit state.
                        pass
                raise
            return tuple(item.extension_id for item in completed)

    async def enable(self, extension_id: str) -> None:
        await self._set_enabled(extension_id, enabled=True)

    async def disable(self, extension_id: str) -> None:
        await self._set_enabled(extension_id, enabled=False)

    async def _set_enabled(self, extension_id: str, *, enabled: bool) -> None:
        async with self._lock:
            item = await self.store.get(extension_id)
            if item is None:
                raise KeyError(extension_id)
            target = "enabled" if enabled else "disabled"
            if item.status == target:
                return
            action = "enable" if enabled else "disable"
            await self._authorize(action, item.manifest)
            await self._audit(action, item.manifest, "requested")
            runtime_action = self.runtime.enable if enabled else self.runtime.disable
            compensation = self.runtime.disable if enabled else self.runtime.enable
            await runtime_action(item.manifest)
            try:
                await self.store.put(InstalledExtension(item.manifest, target))
                await self._audit(action, item.manifest, "succeeded")
            except Exception:
                # A failed outcome audit is still a failed lifecycle operation:
                # restore both the registry snapshot and runtime state.
                try:
                    await self.store.put(item)
                finally:
                    await compensation(item.manifest)
                try:
                    await self._audit(action, item.manifest, "compensated")
                except Exception:
                    pass
                raise

    async def upgrade(self, replacement: ExtensionManifest, *, artifact: Any,
                      previous_artifact: Any) -> None:
        async with self._lock:
            current = await self.store.get(replacement.extension_id)
            if current is None:
                raise KeyError(replacement.extension_id)
            if not artifact or not previous_artifact:
                raise ValueError("verified replacement and rollback artifacts are required")
            if replacement.version == current.manifest.version:
                raise ValueError("replacement version must differ from installed version")
            old_parts = tuple(int(value) for value in current.manifest.version.split("."))
            new_parts = tuple(int(value) for value in replacement.version.split("."))
            if new_parts <= old_parts:
                raise ValueError("upgrade version must be greater than installed version")
            validate_platform_compatibility(
                replacement, platform_api_version=self.lifecycle.platform_api_version,
            )
            installed = await self.store.list()
            available = {key: value.manifest for key, value in installed.items() if key != replacement.extension_id}
            # Validate exact dependency pins and cycles before invoking runtime.
            self.lifecycle.plan_install(replacement, installed=available, available=available)
            await self._authorize("upgrade", replacement)
            await self._audit("upgrade", replacement, "requested")
            await self.runtime.upgrade(current.manifest, replacement, artifact)
            try:
                await self.store.put(InstalledExtension(replacement, current.status, artifact))
                await self._audit("upgrade", replacement, "succeeded")
            except Exception:
                # Keep runtime and registry on the same version if persistence or
                # the terminal audit fails after the runtime swap.
                try:
                    await self.runtime.upgrade(replacement, current.manifest, previous_artifact)
                finally:
                    await self.store.put(current)
                try:
                    await self._audit("upgrade", replacement, "compensated")
                except Exception:
                    pass
                raise

    async def uninstall(self, extension_id: str, *, cascade: bool = False) -> tuple[str, ...]:
        async with self._lock:
            installed = await self.store.list()
            manifests = {key: value.manifest for key, value in installed.items()}
            plan = self.lifecycle.plan_uninstall(extension_id, installed=manifests, cascade=cascade)
            for manifest in plan.manifests:
                await self._authorize("uninstall", manifest)
            removed: list[InstalledExtension] = []
            try:
                for manifest in plan.manifests:
                    await self._audit("uninstall", manifest, "requested")
                    await self.runtime.uninstall(manifest)
                    removed.append(installed[manifest.extension_id])
                    await self.store.remove(manifest.extension_id)
                    await self._audit("uninstall", manifest, "succeeded")
            except Exception:
                for item in reversed(removed):
                    try:
                        await self._audit("rollback_uninstall", item.manifest, "requested")
                        if not item.artifact:
                            raise RuntimeError("rollback artifact is unavailable")
                        await self.runtime.install(item.manifest, item.artifact)
                        if item.status == "enabled":
                            await self.runtime.enable(item.manifest)
                        await self.store.put(item)
                        await self._audit("rollback_uninstall", item.manifest, "succeeded")
                    except Exception:
                        pass
                raise
            return tuple(item.manifest.extension_id for item in removed)
