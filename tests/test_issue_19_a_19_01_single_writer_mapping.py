from __future__ import annotations

import unittest

from agent_platform.data_governance.cutover import CutoverPhase, plan_write
from agent_platform.data_governance.legacy_inventory import (
    DEFAULT_LEGACY_ASSETS,
    MigrationDecision,
    inventory_report,
    validate_business_mappings,
)


class Issue19A1901SingleWriterMappingTests(unittest.TestCase):
    def test_every_cutover_phase_has_exactly_one_authoritative_writer(self):
        for phase in CutoverPhase:
            with self.subTest(phase=phase):
                plan = plan_write(phase=phase)
                self.assertEqual(plan.write_systems, (plan.authoritative_system,))
                self.assertEqual(len(set(plan.write_systems)), 1)
                self.assertTrue(set(plan.write_systems).isdisjoint(plan.replication_targets))

    def test_dual_run_has_one_writer_and_one_replication_sink(self):
        legacy_primary = plan_write(phase=CutoverPhase.DUAL_WRITE)
        self.assertEqual(legacy_primary.authoritative_system, "legacy")
        self.assertEqual(legacy_primary.write_systems, ("legacy",))
        self.assertEqual(legacy_primary.replication_targets, ("target",))

        target_primary = plan_write(phase=CutoverPhase.TARGET_PRIMARY)
        self.assertEqual(target_primary.authoritative_system, "target")
        self.assertEqual(target_primary.write_systems, ("target",))
        self.assertEqual(target_primary.replication_targets, ("legacy",))

    def test_each_legacy_asset_has_source_target_and_migration_owner(self):
        report = inventory_report(DEFAULT_LEGACY_ASSETS)
        self.assertEqual(report["total"], len(DEFAULT_LEGACY_ASSETS))
        for asset in report["assets"]:
            with self.subTest(asset=asset["name"]):
                self.assertTrue(asset["path"].strip())
                self.assertTrue(asset["target"].strip())
                self.assertTrue(asset["owner"].strip())

    def test_each_business_data_mapping_has_unique_source_target_and_owner(self):
        mappings = (
            {"source": "legacy:users", "target": "platform:identity", "owner": "identity"},
            {"source": "legacy:tasks", "target": "platform:tasks", "owner": "runtime"},
        )
        self.assertEqual(validate_business_mappings(mappings), mappings)
        with self.assertRaisesRegex(ValueError, "owner is required"):
            validate_business_mappings(({"source": "legacy:users", "target": "platform:identity"},))
        with self.assertRaisesRegex(ValueError, "sources must be unique"):
            validate_business_mappings((mappings[0], mappings[0]))

    def test_default_business_mappings_are_complete_and_owned(self):
        from agent_platform.data_governance.legacy_inventory import _BUSINESS_MAPPINGS
        mappings = validate_business_mappings(_BUSINESS_MAPPINGS)
        self.assertGreaterEqual(len(mappings), 1)
        self.assertTrue(all(item["source"] and item["target"] and item["owner"] for item in mappings))
        self.assertTrue(any(item["status"] == "gap_requires_domain_design" for item in mappings))


if __name__ == "__main__":
    unittest.main()
