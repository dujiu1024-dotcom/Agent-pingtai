from __future__ import annotations

import unittest

from agent_platform.data_governance.legacy_inventory import (
    DEFAULT_LEGACY_ASSETS, LegacyAsset, MigrationDecision, validate_inventory,
)


class LegacyInventoryTests(unittest.TestCase):
    def test_default_inventory_covers_named_legacy_assets(self):
        inventory = validate_inventory(DEFAULT_LEGACY_ASSETS)
        self.assertEqual(len(inventory), 7)
        self.assertTrue(all(item.decision == MigrationDecision.UNASSESSED for item in inventory))

    def test_active_decision_requires_target_and_unique_paths(self):
        with self.assertRaises(ValueError):
            LegacyAsset("db", "db.py", MigrationDecision.ADAPT, "data")
        item = LegacyAsset("db", "db.py", MigrationDecision.RETIRE, "data")
        with self.assertRaises(ValueError):
            validate_inventory((item, item))
