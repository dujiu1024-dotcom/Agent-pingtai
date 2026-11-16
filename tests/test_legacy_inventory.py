from __future__ import annotations
import unittest
from agent_platform.data_governance.legacy_inventory import (
    DEFAULT_LEGACY_ASSETS, LegacyAsset, MigrationDecision, inventory_report, validate_inventory,
)
class LegacyInventoryTests(unittest.TestCase):
    def test_default_inventory_maps_each_legacy_asset_to_a_candidate(self):
        inventory=validate_inventory(DEFAULT_LEGACY_ASSETS)
        self.assertEqual(len(inventory),7)
        self.assertTrue(all(item.target for item in inventory))
        self.assertTrue(all(item.decision in (MigrationDecision.ADAPT,MigrationDecision.REPLACE) for item in inventory))
        self.assertTrue(all("Candidate" in item.notes or "candidate" in item.notes.lower() for item in inventory))
    def test_active_decision_requires_target_and_unique_paths(self):
        with self.assertRaises(ValueError): LegacyAsset("db","db.py",MigrationDecision.ADAPT,"data")
        item=LegacyAsset("db","db.py",MigrationDecision.RETIRE,"data")
        with self.assertRaises(ValueError): validate_inventory((item,item))
    def test_default_report_keeps_unverified_mapping_as_candidate_not_completion(self):
        report=inventory_report()
        self.assertEqual(report["total"],7)
        self.assertEqual(report["assessed"],7)
        self.assertEqual(report["unassessed"],0)
        self.assertEqual(report["mapping_candidates"],7)
        self.assertEqual(report["by_decision"]["adapt"],6)
        self.assertEqual(report["by_decision"]["replace"],1)
        self.assertTrue(all("unverified" in item["notes"].lower() or "validate" in item["notes"].lower()
                            or "candidate" in item["notes"].lower() or "compare" in item["notes"].lower()
                            or "reconcile" in item["notes"].lower() for item in report["assets"]))
    def test_report_is_sorted_and_counts_explicit_decisions(self):
        assets=(LegacyAsset("zeta","zeta.py",MigrationDecision.RETIRE,"runtime"),
                LegacyAsset("alpha","alpha.py",MigrationDecision.ADAPT,"data","new.data"))
        report=inventory_report(assets)
        self.assertEqual([item["name"] for item in report["assets"]],["alpha","zeta"])
        self.assertEqual(report["assessed"],2)
        self.assertEqual(report["by_decision"]["adapt"],1)
        self.assertEqual(report["by_decision"]["retire"],1)
    def test_rejects_string_decisions_and_invalid_inventory_entries(self):
        with self.assertRaisesRegex(ValueError,"MigrationDecision"):
            LegacyAsset("bad","bad.py","adapt","owner","target")
        with self.assertRaisesRegex(ValueError,"LegacyAsset"): validate_inventory([object()])
if __name__=="__main__": unittest.main()
