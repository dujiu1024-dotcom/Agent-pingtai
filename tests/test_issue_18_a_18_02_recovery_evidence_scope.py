from __future__ import annotations

import unittest
from datetime import datetime, timezone

from scripts.disaster_recovery import validate_record


def postgres_record() -> dict:
    return {
        "exercise_id": "DR-ISSUE18-POSTGRES-001",
        "scope": "postgres",
        "components_in_scope": ["postgres"],
        "last_known_good_write_at": "2026-11-18T10:00:00.125Z",
        "incident_declared_at": "2026-11-18T10:00:01.375Z",
        "traffic_resumed_at": "2026-11-18T10:00:04.625Z",
        "component_checks": {"postgres": True},
        "consistency_checks": {"postgres_restore_marker_verified": True},
        "reviewer_signoff": True,
        "evidence_references": ["artifact://postgres-restore-drill/report.json"],
    }


class PostgresRecoveryEvidenceScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        from pathlib import Path
        import json

        root = Path(__file__).resolve().parents[1]
        self.policy = json.loads(
            (root / "deploy/disaster-recovery-policy.json").read_text(encoding="utf-8")
        )

    def test_postgres_scope_uses_component_gates_and_preserves_millisecond_precision(self):
        report = validate_record(postgres_record(), self.policy)

        self.assertTrue(report["passed"], report["failures"])
        self.assertEqual(report["scope"], "postgres")
        self.assertEqual(report["measured_rpo_seconds"], 1.25)
        self.assertEqual(report["measured_rto_seconds"], 3.25)
        self.assertEqual(report["failures"], [])

    def test_postgres_scope_fails_when_restore_marker_is_missing(self):
        record = postgres_record()
        record["consistency_checks"] = {}

        report = validate_record(record, self.policy)

        self.assertFalse(report["passed"])
        self.assertIn(
            "consistency gate missing or failed: postgres_restore_marker_verified",
            report["failures"],
        )

    def test_postgres_scope_fails_when_component_check_is_not_true(self):
        record = postgres_record()
        record["component_checks"]["postgres"] = 1

        report = validate_record(record, self.policy)

        self.assertFalse(report["passed"])
        self.assertIn("component check missing or failed: postgres", report["failures"])

    def test_postgres_scope_does_not_require_unrelated_full_platform_gates(self):
        record = postgres_record()
        record.pop("failover_checks", None)

        report = validate_record(record, self.policy)

        self.assertTrue(report["passed"], report["failures"])
        self.assertFalse(any("former_primary_fenced" in item for item in report["failures"]))

    def test_component_scope_requires_a_non_empty_unique_component_list(self):
        for components in (None, [], "postgres", ["postgres", "postgres"], [""]):
            with self.subTest(components=components):
                record = postgres_record()
                record["components_in_scope"] = components

                report = validate_record(record, self.policy)

                self.assertFalse(report["passed"])
                self.assertIn(
                    "components_in_scope must be a non-empty list of unique component names",
                    report["failures"],
                )

    def test_component_scope_rejects_non_object_consistency_checks(self):
        record = postgres_record()
        record["consistency_checks"] = ["postgres_restore_marker_verified"]

        report = validate_record(record, self.policy)

        self.assertFalse(report["passed"])
        self.assertIn("consistency_checks must be an object", report["failures"])


if __name__ == "__main__":
    unittest.main()
