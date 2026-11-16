from __future__ import annotations

import unittest

from agent_platform.extensions.executor import ExtensionLifecycleExecutor
from agent_platform.extensions.lifecycle import ExtensionLifecycle, ExtensionLifecyclePlan
from agent_platform.extensions.manifest import ExtensionManifest


def manifest(name, dependencies=(), version="1.0.0"):
    return ExtensionManifest(
        name, version, "1.0.0", ("tools.invoke",),
        tuple(dependencies), "plugin/main.py",
        {dependency: "1.0.0" for dependency in dependencies},
    )


class ExtensionLifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.lifecycle = ExtensionLifecycle(platform_api_version="1.0.0")

    def test_install_plan_is_dependency_first_and_deterministic(self):
        root = manifest("root-extension", ("base-one", "base-two"))
        available = {
            "base-one": manifest("base-one", ("base-core",)),
            "base-two": manifest("base-two"),
            "base-core": manifest("base-core"),
        }
        plan = self.lifecycle.plan_install(root, installed={}, available=available)
        self.assertEqual(
            plan.ordered_extension_ids,
            ("base-core", "base-one", "base-two", "root-extension"),
        )

    def test_install_rejects_missing_dependencies_and_cycles(self):
        with self.assertRaisesRegex(ValueError, "missing extension dependency"):
            self.lifecycle.plan_install(
                manifest("root-extension", ("missing-base",)),
                installed={}, available={},
            )
        cycle = {
            "base-one": manifest("base-one", ("base-two",)),
            "base-two": manifest("base-two", ("base-one",)),
        }
        with self.assertRaisesRegex(ValueError, "cycle"):
            self.lifecycle.plan_install(
                manifest("root-extension", ("base-one",)),
                installed={}, available=cycle,
            )

    def test_uninstall_blocks_live_dependents_and_cascade_is_dependents_first(self):
        installed = {
            "base-core": manifest("base-core"),
            "base-one": manifest("base-one", ("base-core",)),
            "root-extension": manifest("root-extension", ("base-one",)),
        }
        with self.assertRaisesRegex(ValueError, "required by"):
            self.lifecycle.plan_uninstall("base-core", installed=installed)
        plan = self.lifecycle.plan_uninstall(
            "base-core", installed=installed, cascade=True,
        )
        self.assertEqual(
            plan.ordered_extension_ids,
            ("root-extension", "base-one", "base-core"),
        )


    def test_install_requires_exact_dependency_versions(self):
        with self.assertRaisesRegex(ValueError, "exact version pin"):
            ExtensionManifest(
                "root-extension", "1.0.0", "1.0.0", ("tools.invoke",),
                ("base-one",), "plugin/main.py",
            )
        root = ExtensionManifest(
            "root-extension", "1.0.0", "1.0.0", ("tools.invoke",),
            ("base-one",), "plugin/main.py", {"base-one": "2.0.0"},
        )
        with self.assertRaisesRegex(ValueError, "version mismatch"):
            self.lifecycle.plan_install(
                root, installed={},
                available={"base-one": manifest("base-one", version="1.0.0")},
            )

    def test_uninstall_rejects_unknown_extension(self):
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.lifecycle.plan_uninstall("missing", installed={})


    async def test_executor_authorizes_every_manifest_before_side_effects(self):
        base = manifest("base-extension")
        root = manifest("root-extension", ("base-extension",))
        calls = []
        class Runtime:
            async def install(self, item):
                calls.append(("install", item.extension_id))
            async def uninstall(self, item):
                calls.append(("uninstall", item.extension_id))
        executor = ExtensionLifecycleExecutor(
            runtime=Runtime(),
            authorize=lambda action, item: item.extension_id != "root-extension",
            audit=lambda action, item, outcome: calls.append(("audit", item.extension_id, outcome)),
        )
        plan = self.lifecycle.plan_install(
            root, installed={}, available={"base-extension": base},
        )
        with self.assertRaises(PermissionError):
            await executor.execute(plan)
        self.assertEqual(calls, [])

    async def test_executor_rolls_back_partial_install_and_audits(self):
        base = manifest("base-extension")
        root = manifest("root-extension", ("base-extension",))
        calls = []
        class Runtime:
            async def install(self, item):
                calls.append(("install", item.extension_id))
                if item.extension_id == "root-extension":
                    raise RuntimeError("install failed")
            async def uninstall(self, item):
                calls.append(("uninstall", item.extension_id))
        executor = ExtensionLifecycleExecutor(
            runtime=Runtime(),
            authorize=lambda action, item: True,
            audit=lambda action, item, outcome: calls.append(("audit", action, item.extension_id, outcome)),
        )
        plan = self.lifecycle.plan_install(
            root, installed={}, available={"base-extension": base},
        )
        with self.assertRaisesRegex(RuntimeError, "install failed"):
            await executor.execute(plan)
        self.assertIn(("uninstall", "base-extension"), calls)
        self.assertIn(("audit", "rollback_install", "base-extension", "succeeded"), calls)

    async def test_executor_uninstall_restores_removed_extensions_after_failure(self):
        base = manifest("base-extension")
        root = manifest("root-extension", ("base-extension",))
        calls = []
        class Runtime:
            async def install(self, item):
                calls.append(("install", item.extension_id))
            async def uninstall(self, item):
                calls.append(("uninstall", item.extension_id))
                if item.extension_id == "base-extension":
                    raise RuntimeError("uninstall failed")
        executor = ExtensionLifecycleExecutor(
            runtime=Runtime(),
            authorize=lambda action, item: True,
            audit=lambda action, item, outcome: calls.append(("audit", action, item.extension_id, outcome)),
        )
        plan = self.lifecycle.plan_uninstall(
            "base-extension",
            installed={"base-extension": base, "root-extension": root},
            cascade=True,
        )
        with self.assertRaisesRegex(RuntimeError, "uninstall failed"):
            await executor.execute(plan)
        self.assertEqual(
            [item for item in calls if item[0] == "install"],
            [("install", "root-extension")],
        )

    async def test_executor_rejects_misaligned_or_unsupported_plans(self):
        calls = []
        class Runtime:
            async def install(self, item): calls.append(item.extension_id)
            async def uninstall(self, item): calls.append(item.extension_id)
        executor = ExtensionLifecycleExecutor(
            runtime=Runtime(), authorize=lambda action, item: True,
            audit=lambda action, item, outcome: None,
        )
        with self.assertRaisesRegex(ValueError, "unsupported"):
            await executor.execute(ExtensionLifecyclePlan("remove", (), ()))
        with self.assertRaisesRegex(ValueError, "do not align"):
            await executor.execute(ExtensionLifecyclePlan("install", ("base-extension",), ()))
        self.assertEqual(calls, [])



if __name__ == "__main__":
    unittest.main()
