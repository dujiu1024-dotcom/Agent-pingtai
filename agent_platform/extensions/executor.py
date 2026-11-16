"""Guarded extension lifecycle execution with authorization, audit and rollback."""
from __future__ import annotations

import inspect
from typing import Any, Callable, Protocol

from agent_platform.extensions.lifecycle import ExtensionLifecyclePlan
from agent_platform.extensions.manifest import ExtensionManifest


class ExtensionRuntime(Protocol):
    async def install(self, manifest: ExtensionManifest) -> None: ...
    async def uninstall(self, manifest: ExtensionManifest) -> None: ...


Authorize = Callable[[str, ExtensionManifest], Any]
Audit = Callable[[str, ExtensionManifest, str], Any]


async def _invoke(callback: Callable[..., Any], *args: Any) -> Any:
    result = callback(*args)
    if inspect.isawaitable(result):
        return await result
    return result


class ExtensionLifecycleExecutor:
    """Execute precomputed plans only after all permissions are checked.

    Authorization and audit callbacks may be synchronous or asynchronous.
    Audit intent is recorded before each side effect. Partial installs and
    uninstalls are compensated when a later operation fails.
    """

    def __init__(
        self,
        *,
        runtime: ExtensionRuntime,
        authorize: Authorize,
        audit: Audit,
    ) -> None:
        if not callable(authorize) or not callable(audit):
            raise TypeError("authorize and audit callbacks are required")
        self.runtime = runtime
        self.authorize = authorize
        self.audit = audit

    async def execute(self, plan: ExtensionLifecyclePlan) -> tuple[str, ...]:
        if plan.action not in {"install", "uninstall"}:
            raise ValueError("unsupported extension lifecycle action")
        if len(plan.ordered_extension_ids) != len(plan.manifests):
            raise ValueError("lifecycle plan ids and manifests do not align")
        if tuple(item.extension_id for item in plan.manifests) != plan.ordered_extension_ids:
            raise ValueError("lifecycle plan order does not match its manifests")

        for manifest in plan.manifests:
            allowed = await _invoke(self.authorize, plan.action, manifest)
            if not allowed:
                raise PermissionError(f"not authorized to {plan.action} {manifest.extension_id}")

        if plan.action == "install":
            return await self._install(plan.manifests)
        return await self._uninstall(plan.manifests)

    async def _install(self, manifests: tuple[ExtensionManifest, ...]) -> tuple[str, ...]:
        installed: list[ExtensionManifest] = []
        try:
            for manifest in manifests:
                await _invoke(self.audit, "install", manifest, "requested")
                await self.runtime.install(manifest)
                installed.append(manifest)
                await _invoke(self.audit, "install", manifest, "succeeded")
        except Exception:
            for manifest in reversed(installed):
                await _invoke(self.audit, "rollback_install", manifest, "requested")
                await self.runtime.uninstall(manifest)
                await _invoke(self.audit, "rollback_install", manifest, "succeeded")
            raise
        return tuple(item.extension_id for item in installed)

    async def _uninstall(self, manifests: tuple[ExtensionManifest, ...]) -> tuple[str, ...]:
        removed: list[ExtensionManifest] = []
        try:
            for manifest in manifests:
                await _invoke(self.audit, "uninstall", manifest, "requested")
                await self.runtime.uninstall(manifest)
                removed.append(manifest)
                await _invoke(self.audit, "uninstall", manifest, "succeeded")
        except Exception:
            # Restore dependencies before dependants after a partial removal.
            for manifest in reversed(removed):
                await _invoke(self.audit, "rollback_uninstall", manifest, "requested")
                await self.runtime.install(manifest)
                await _invoke(self.audit, "rollback_uninstall", manifest, "succeeded")
            raise
        return tuple(item.extension_id for item in removed)
