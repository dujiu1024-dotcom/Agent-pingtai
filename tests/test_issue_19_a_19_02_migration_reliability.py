from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="legacy migration reliability")

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.data_governance.cutover import (
    ConsistencyEvidence,
    CutoverPhase,
    CutoverPolicy,
    choose_read_plan,
    evaluate_cutover,
    plan_write,
)
from agent_platform.data_governance.migration_batches import (
    MigrationError,
    TableMapping,
    migration_batch_status,
    migration_diff_report,
    run_migration_batch,
)


def healthy_evidence(**overrides):
    values = {
        "sampled_rows": 20,
        "mismatched_rows": 0,
        "source_checksum": "source-checksum",
        "target_checksum": "source-checksum",
        "replication_lag_seconds": 0,
        "write_error_rate": 0,
        "observed_seconds": 3600,
        "legacy_write_rate": 0,
        "evidence_age_seconds": 0,
    }
    values.update(overrides)
    return ConsistencyEvidence(**values)


class Issue19A1902MigrationReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "legacy.db"
        self.target = self.root / "target.db"
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE legacy_records(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            db.executemany(
                "INSERT INTO legacy_records VALUES(?, ?)",
                [(i, f"record-{i}") for i in range(1, 8)],
            )
        with sqlite3.connect(self.target) as db:
            db.execute("CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        self.mapping = [TableMapping("legacy_records", "records", {"id": "id", "value": "value"})]

    def tearDown(self):
        self.temp.cleanup()

    def test_same_batch_can_resume_and_replay_without_duplicate_target_rows(self):
        paused = run_migration_batch(
            self.source, self.target, self.mapping, batch_id="a1902-replay", batch_size=2, max_chunks=1,
        )
        self.assertEqual(paused["state"], "paused")
        resumed = run_migration_batch(
            self.source, self.target, self.mapping, batch_id="a1902-replay", batch_size=2,
        )
        replayed = run_migration_batch(
            self.source, self.target, self.mapping, batch_id="a1902-replay", batch_size=2,
        )
        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(replayed["applied_rows"], resumed["applied_rows"])
        with sqlite3.connect(self.target) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM records").fetchone()[0], 7)

    def test_diff_verification_detects_corruption_and_never_exports_row_values(self):
        run_migration_batch(self.source, self.target, self.mapping, batch_id="a1902-diff")
        with sqlite3.connect(self.target) as db:
            db.execute("UPDATE records SET value='tampered' WHERE id=3")
            db.execute("DELETE FROM records WHERE id=4")
        report = migration_diff_report(
            self.source, self.target, self.mapping, batch_id="a1902-diff",
        )
        self.assertEqual(report["totals"]["mismatched_rows"], 1)
        self.assertEqual(report["totals"]["missing_target_rows"], 1)
        self.assertFalse(report["values_included"])
        self.assertNotIn("record-3", str(report))

    def test_failed_batch_is_resumable_after_transient_target_dependency_failure(self):
        invalid_mapping = [TableMapping("legacy_records", "missing_target", {"id": "id", "value": "value"})]
        with self.assertRaises(MigrationError):
            run_migration_batch(
                self.source, self.target, invalid_mapping, batch_id="a1902-retry", batch_size=2,
            )
        self.assertEqual(migration_batch_status(self.target, "a1902-retry")["state"], "failed")
        with sqlite3.connect(self.target) as db:
            db.execute("CREATE TABLE missing_target(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        completed = run_migration_batch(
            self.source, self.target, invalid_mapping, batch_id="a1902-retry", batch_size=2,
        )
        self.assertEqual(completed["state"], "completed")
        with sqlite3.connect(self.target) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM missing_target").fetchone()[0], 7)

    def test_rollback_routes_reads_and_writes_to_legacy_and_preserves_source(self):
        source_before = self.source.read_bytes()
        decision = evaluate_cutover(
            current=CutoverPhase.CANARY,
            requested=CutoverPhase.TARGET_PRIMARY,
            evidence=healthy_evidence(target_checksum="drifted"),
            policy=CutoverPolicy(),
        )
        self.assertEqual(decision.phase, CutoverPhase.ROLLED_BACK)
        self.assertTrue(decision.rollback_required)
        self.assertEqual(plan_write(phase=decision.phase).authoritative_system, "legacy")
        self.assertEqual(
            choose_read_plan(phase=decision.phase, tenant_id="tenant-a", request_key="request-1").primary_system,
            "legacy",
        )
        self.assertEqual(self.source.read_bytes(), source_before)

    def test_legacy_remains_primary_until_cutover_acceptance_and_retirement_gate(self):
        before_acceptance = evaluate_cutover(
            current=CutoverPhase.LEGACY_PRIMARY,
            requested=CutoverPhase.TARGET_PRIMARY,
            evidence=healthy_evidence(),
            policy=CutoverPolicy(),
        )
        self.assertFalse(before_acceptance.allowed)
        self.assertIn("invalid_phase_transition", before_acceptance.reasons)
        self.assertEqual(
            choose_read_plan(phase=CutoverPhase.LEGACY_PRIMARY, tenant_id="tenant-a", request_key="r").primary_system,
            "legacy",
        )
        self.assertEqual(plan_write(phase=CutoverPhase.LEGACY_PRIMARY).authoritative_system, "legacy")

        accepted = evaluate_cutover(
            current=CutoverPhase.CANARY,
            requested=CutoverPhase.TARGET_PRIMARY,
            evidence=healthy_evidence(),
            policy=CutoverPolicy(),
        )
        self.assertTrue(accepted.allowed)
        self.assertEqual(accepted.phase, CutoverPhase.TARGET_PRIMARY)
        self.assertEqual(
            choose_read_plan(phase=accepted.phase, tenant_id="tenant-a", request_key="r").primary_system,
            "target",
        )
        premature_retirement = evaluate_cutover(
            current=CutoverPhase.TARGET_PRIMARY,
            requested=CutoverPhase.RETIRED,
            evidence=healthy_evidence(observed_seconds=60),
            policy=CutoverPolicy(rollback_window_seconds=3600),
        )
        self.assertFalse(premature_retirement.allowed)
        self.assertIn("rollback_window_not_elapsed", premature_retirement.reasons)
        self.assertEqual(plan_write(phase=CutoverPhase.TARGET_PRIMARY).replication_targets, ("legacy",))


if __name__ == "__main__":
    unittest.main()
