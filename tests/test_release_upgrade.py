from __future__ import annotations

import copy
import unittest

from scripts.release_upgrade import build_plan, build_rollback, validate_upgrade_manifest


def manifest():
    return {
        "release_id": "release-2.1.0",
        "from_version": "2.0.3",
        "to_version": "2.1.0",
        "image_digest": "registry.example/agent@sha256:" + "a" * 64,
        "config_revision": "config-2026-11-17-2",
        "previous_config_revision": "config-2026-11-10-1",
        "compatibility": {
            "min_app_version": "2.0.0", "max_app_version": "2.1.0",
            "min_schema_version": "10", "max_schema_version": "11",
            "window_minutes": 1440, "old_and_new_coexist": True,
        },
        "migration": {
            "preflight_passed": True, "backup_ref": "backup://verified/2026-11-17",
            "expand_contract": True, "destructive": True,
            "contract_after_compatibility_window": True,
            "rollback_strategy": "compensating_migration",
            "compensating_migration_ref": "migrations/rollback-210.sql",
            "migration_ref": "migrations/expand-210.sql",
        },
        "rollout": {"batches": [5, 25, 70], "health_gate": True, "pause_minutes": 10},
        "rollback": {
            "previous_image_digest": "registry.example/agent@sha256:" + "b" * 64,
            "previous_config_revision": "config-2026-11-10-1",
            "config_restore_ref": "config-snapshots/config-2026-11-10-1",
            "automatic_on_health_failure": True,
        },
    }


class ReleaseUpgradeTests(unittest.TestCase):
    def test_valid_manifest_and_ordered_rollout_plan(self):
        doc = manifest()
        self.assertEqual(validate_upgrade_manifest(doc), [])
        plan = build_plan(doc)
        names = [item["step"] for item in plan]
        self.assertLess(names.index("migration_preflight"), names.index("expand_migration"))
        self.assertLess(names.index("compatibility_window"), names.index("rolling_batch"))
        self.assertEqual([x["traffic_percent"] for x in plan if x["step"] == "rolling_batch"], [5, 25, 70])
        self.assertEqual(names[-1], "defer_contract_migration")

    def test_missing_preflight_backup_and_health_gate_block(self):
        doc = manifest()
        doc["migration"]["preflight_passed"] = False
        doc["migration"]["backup_ref"] = ""
        doc["rollout"]["health_gate"] = False
        errors = validate_upgrade_manifest(doc)
        self.assertTrue(any("preflight_passed" in e for e in errors))
        self.assertTrue(any("backup_ref" in e for e in errors))
        self.assertTrue(any("health_gate" in e for e in errors))

    def test_destructive_migration_must_wait_for_compatibility_window(self):
        doc = manifest()
        doc["migration"]["contract_after_compatibility_window"] = False
        errors = validate_upgrade_manifest(doc)
        self.assertTrue(any("destructive contract must wait" in e for e in errors))

    def test_rolling_percentages_must_sum_to_100(self):
        doc = manifest()
        doc["rollout"]["batches"] = [10, 30, 50]
        self.assertTrue(any("must sum to 100" in e for e in validate_upgrade_manifest(doc)))

    def test_old_and_new_must_coexist_during_window(self):
        doc = manifest()
        doc["compatibility"]["old_and_new_coexist"] = False
        self.assertTrue(any("old_and_new_coexist" in e for e in validate_upgrade_manifest(doc)))

    def test_rollback_restores_image_and_config_and_compensates_schema(self):
        plan = build_rollback(manifest())
        names = [item["step"] for item in plan]
        self.assertIn("restore_application_image", names)
        self.assertIn("restore_configuration", names)
        self.assertIn("apply_compensating_migration", names)
        self.assertEqual(names[-1], "verify_previous_version_health")

    def test_no_schema_rollback_means_preserve_backward_compatible_schema(self):
        doc = manifest()
        doc["migration"]["rollback_strategy"] = "none"
        plan = build_rollback(doc)
        self.assertIn("preserve_backward_compatible_schema", [x["step"] for x in plan])

    def test_restore_backup_requires_restore_drill_evidence(self):
        doc = manifest()
        doc["migration"]["rollback_strategy"] = "restore_backup"
        self.assertTrue(any("restore_drill_ref" in e for e in validate_upgrade_manifest(doc)))

    def test_rollback_config_revision_must_match_snapshot(self):
        doc = manifest()
        doc["rollback"]["previous_config_revision"] = "wrong-revision"
        self.assertTrue(any("does not match manifest" in e for e in validate_upgrade_manifest(doc)))

    def test_semantic_version_must_increase(self):
        doc = manifest()
        doc["to_version"] = "2.0.3"
        self.assertTrue(any("must be greater" in e for e in validate_upgrade_manifest(doc)))


if __name__ == "__main__":
    unittest.main()
