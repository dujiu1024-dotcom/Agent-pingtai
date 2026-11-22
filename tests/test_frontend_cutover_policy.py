"""Offline tests for the Issue #752 frontend cutover safety contract."""
import unittest

from scripts.frontend_cutover_policy import CutoverRecordError, ROLLBACK_ACTION, validate_record


def valid_record():
    return {
        "stage": "internal",
        "legacy_artifact_sha": "legacy-build-abc123",
        "candidate_artifact_sha": "candidate-build-def456",
        "active_entry": "candidate",
        "rollback_target_sha": "legacy-build-abc123",
        "config_checksum": "sha256:proxy-config",
        "canary_percent": 5,
        "sticky_routing": True,
        "health_gates": {
            "bootstrap": True,
            "core_routes": True,
            "api_errors": True,
            "frontend_exceptions": True,
            "sse_reconnect": True,
            "file_operations": True,
        },
        "p0_p1_open": False,
        "preserve_in_flight_work": True,
        "business_data_rollback": False,
    }


class FrontendCutoverPolicyTests(unittest.TestCase):
    def test_healthy_internal_canary_holds_or_continues_without_mutation(self):
        result = validate_record(valid_record())
        self.assertEqual(result["decision"], "hold_or_continue")
        self.assertFalse(result["mutates_live_system"])
        self.assertIsNone(result["rollback_target_sha"])

    def test_failed_health_gate_rolls_back_candidate_entry_only(self):
        record = valid_record()
        record["health_gates"]["bootstrap"] = False
        result = validate_record(record)
        self.assertEqual(result["decision"], ROLLBACK_ACTION)
        self.assertEqual(result["rollback_target_sha"], "legacy-build-abc123")
        self.assertTrue(result["preserve_in_flight_work"])
        self.assertFalse(result["business_data_rollback"])
        self.assertFalse(result["mutates_live_system"])

    def test_open_p0_p1_forces_entry_rollback(self):
        record = valid_record()
        record["p0_p1_open"] = True
        result = validate_record(record)
        self.assertEqual(result["decision"], ROLLBACK_ACTION)

    def test_rejects_non_sticky_canary(self):
        record = valid_record()
        record["sticky_routing"] = False
        with self.assertRaisesRegex(CutoverRecordError, "sticky_routing"):
            validate_record(record)

    def test_rejects_business_data_rollback(self):
        record = valid_record()
        record["business_data_rollback"] = True
        with self.assertRaisesRegex(CutoverRecordError, "business data"):
            validate_record(record)

    def test_rejects_rollback_target_that_is_not_legacy(self):
        record = valid_record()
        record["rollback_target_sha"] = "unknown-build"
        with self.assertRaisesRegex(CutoverRecordError, "rollback target"):
            validate_record(record)

    def test_rejects_invalid_canary_percent(self):
        record = valid_record()
        record["canary_percent"] = 101
        with self.assertRaisesRegex(CutoverRecordError, "canary_percent"):
            validate_record(record)

    def test_rejects_missing_health_evidence(self):
        record = valid_record()
        record["health_gates"] = {}
        with self.assertRaisesRegex(CutoverRecordError, "health_gates"):
            validate_record(record)


if __name__ == "__main__":
    unittest.main()
