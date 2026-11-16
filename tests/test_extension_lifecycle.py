from __future__ import annotations

import unittest

from agent_platform.extensions.lifecycle import ExtensionLifecycle
from agent_platform.extensions.manifest import ExtensionManifest


def manifest(name, dependencies=(), version="1.0.0"):
    return ExtensionManifest(
        name, version, "1.0.0", ("tools.invoke",),
        tuple(dependencies), "plugin/main.py",
    )


class ExtensionLifecycleTests(unittest.TestCase):
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

    def test_uninstall_rejects_unknown_extension(self):
        with self.assertRaisesRegex(ValueError, "not installed"):
            self.lifecycle.plan_uninstall("missing", installed={})


if __name__ == "__main__":
    unittest.main()
