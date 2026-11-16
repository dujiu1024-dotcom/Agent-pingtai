from __future__ import annotations

import unittest

from agent_platform.extensions.manifest import ExtensionManifest
from agent_platform.extensions.dependency_graph import resolve_install_order, uninstall_impact


def manifest(extension_id, dependencies=()):
    return ExtensionManifest(
        extension_id=extension_id, version="1.0.0", platform_api="1.0.0",
        permissions=(), dependencies=tuple(dependencies),
        dependency_versions={item: ">=1.0.0" for item in dependencies},
    )


class ExtensionDependencyGraphTests(unittest.TestCase):
    def test_install_dependencies_first_and_uninstall_dependants_first(self):
        items = [manifest("base"), manifest("middle", ("base",)), manifest("app", ("middle",))]
        self.assertEqual(resolve_install_order(items), ("base", "middle", "app"))
        self.assertEqual(uninstall_impact("base", items), ("app", "middle", "base"))

    def test_missing_dependency_and_cycle_are_rejected(self):
        with self.assertRaises(ValueError):
            resolve_install_order([manifest("app", ("missing",))])
        with self.assertRaises(ValueError):
            resolve_install_order([manifest("a", ("b",)), manifest("b", ("a",))])
