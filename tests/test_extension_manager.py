from __future__ import annotations

import unittest

from agent_platform.extensions.manager import (
    ExtensionLifecycleManager, InMemoryExtensionStateStore,
)
from agent_platform.extensions.manifest import ExtensionManifest


def make_manifest(name, version="1.0.0", dependencies=(), permissions=("tools.invoke",)):
    return ExtensionManifest(
        name, version, "1.0.0", tuple(permissions), tuple(dependencies),
        "plugin/main.py", {dependency: "1.0.0" for dependency in dependencies},
    )


class Runtime:
    def __init__(self):
        self.calls = []
        self.fail_install = None
    async def install(self, manifest, artifact):
        self.calls.append(("install", manifest.extension_id, artifact))
        if manifest.extension_id == self.fail_install:
            raise RuntimeError("install failure")
    async def enable(self, manifest): self.calls.append(("enable", manifest.extension_id))
    async def disable(self, manifest): self.calls.append(("disable", manifest.extension_id))
    async def upgrade(self, old, new, artifact):
        self.calls.append(("upgrade", old.version, new.version, artifact))
    async def uninstall(self, manifest): self.calls.append(("uninstall", manifest.extension_id))


class ExtensionManagerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.store = InMemoryExtensionStateStore()
        self.runtime = Runtime()
        self.authorizations = []
        self.audit_events = []
        self.manager = ExtensionLifecycleManager(
            store=self.store, runtime=self.runtime, platform_api_version="1.0.0",
            authorize=lambda action, manifest, permission: self.authorizations.append(
                (action, manifest.extension_id, permission)
            ) or True,
            audit=lambda action, manifest, outcome: self.audit_events.append(
                (action, manifest.extension_id, outcome)
            ),
        )

    async def test_install_enable_disable_upgrade_and_uninstall(self):
        base = make_manifest("base-extension")
        root = make_manifest("root-extension", dependencies=("base-extension",))
        installed = await self.manager.install(
            root, artifact=b"root-package",
            available={"base-extension": base},
            dependency_artifacts={"base-extension": b"base-package"},
        )
        self.assertEqual(installed, ("base-extension", "root-extension"))
        self.assertEqual((await self.store.get("root-extension")).status, "disabled")
        await self.manager.enable("root-extension")
        self.assertEqual((await self.store.get("root-extension")).status, "enabled")
        await self.manager.disable("root-extension")
        self.assertEqual((await self.store.get("root-extension")).status, "disabled")

        upgraded = make_manifest("root-extension", version="2.0.0", dependencies=("base-extension",))
        await self.manager.upgrade(upgraded, artifact=b"v2-package", previous_artifact=b"root-package")
        current = await self.store.get("root-extension")
        self.assertEqual(current.manifest.version, "2.0.0")
        self.assertEqual(current.artifact, b"v2-package")

        with self.assertRaisesRegex(ValueError, "required by"):
            await self.manager.uninstall("base-extension")
        removed = await self.manager.uninstall("base-extension", cascade=True)
        self.assertEqual(removed, ("root-extension", "base-extension"))
        self.assertEqual(await self.store.list(), {})

    async def test_permission_declaration_is_checked_before_side_effects(self):
        manager = ExtensionLifecycleManager(
            store=self.store, runtime=self.runtime, platform_api_version="1.0.0",
            authorize=lambda action, manifest, permission: permission != "secrets.read",
            audit=lambda *args: None,
        )
        manifest = make_manifest("sensitive-extension", permissions=("tools.invoke", "secrets.read"))
        with self.assertRaises(PermissionError):
            await manager.install(manifest, artifact=b"pkg")
        self.assertEqual(self.runtime.calls, [])

    async def test_partial_install_compensates_completed_dependencies(self):
        self.runtime.fail_install = "root-extension"
        base = make_manifest("base-extension")
        root = make_manifest("root-extension", dependencies=("base-extension",))
        with self.assertRaisesRegex(RuntimeError, "install failure"):
            await self.manager.install(
                root, artifact=b"root-package",
                available={"base-extension": base},
                dependency_artifacts={"base-extension": b"base-package"},
            )
        self.assertIn(("uninstall", "base-extension"), self.runtime.calls)
        self.assertEqual(await self.store.list(), {})

    async def test_upgrade_requires_rollback_artifact_and_monotonic_version(self):
        manifest = make_manifest("sample-extension")
        await self.manager.install(manifest, artifact=b"v1")
        with self.assertRaisesRegex(ValueError, "rollback artifacts"):
            await self.manager.upgrade(make_manifest("sample-extension", "2.0.0"), artifact=b"v2", previous_artifact=None)
        with self.assertRaisesRegex(ValueError, "greater"):
            await self.manager.upgrade(manifest, artifact=b"v1b", previous_artifact=b"v1")


    async def test_enable_terminal_audit_failure_restores_runtime_and_registry(self):
        manifest = make_manifest("audit-enable-extension")
        await self.manager.install(manifest, artifact=b"v1")
        original_audit = self.manager.audit
        failed = False

        def audit(action, item, outcome):
            nonlocal failed
            if action == "enable" and outcome == "succeeded" and not failed:
                failed = True
                raise RuntimeError("audit unavailable")
            return original_audit(action, item, outcome)

        self.manager.audit = audit
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await self.manager.enable("audit-enable-extension")

        stored = await self.store.get("audit-enable-extension")
        self.assertEqual(stored.status, "disabled")
        self.assertIn(("enable", "audit-enable-extension"), self.runtime.calls)
        self.assertIn(("disable", "audit-enable-extension"), self.runtime.calls)

    async def test_upgrade_terminal_audit_failure_restores_previous_version(self):
        manifest = make_manifest("audit-upgrade-extension")
        await self.manager.install(manifest, artifact=b"v1")
        original_audit = self.manager.audit
        failed = False

        def audit(action, item, outcome):
            nonlocal failed
            if action == "upgrade" and outcome == "succeeded" and not failed:
                failed = True
                raise RuntimeError("audit unavailable")
            return original_audit(action, item, outcome)

        self.manager.audit = audit
        replacement = make_manifest("audit-upgrade-extension", version="2.0.0")
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await self.manager.upgrade(
                replacement, artifact=b"v2", previous_artifact=b"v1",
            )

        stored = await self.store.get("audit-upgrade-extension")
        self.assertEqual(stored.manifest.version, "1.0.0")
        self.assertEqual(stored.artifact, b"v1")
        self.assertIn(("upgrade", "1.0.0", "2.0.0", b"v2"), self.runtime.calls)
        self.assertIn(("upgrade", "2.0.0", "1.0.0", b"v1"), self.runtime.calls)

if __name__ == "__main__":
    unittest.main()
