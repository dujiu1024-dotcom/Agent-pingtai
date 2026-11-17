from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.data_governance.migration_batches import (
    MigrationError,
    TableMapping,
    migration_batch_status,
    migration_diff_report,
    run_migration_batch,
)


class MigrationBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.source = self.root / "source.db"
        self.target = self.root / "target.db"
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE old_users(id INTEGER PRIMARY KEY, display_name TEXT, active INTEGER)")
            db.executemany("INSERT INTO old_users VALUES(?,?,?)",
                           [(i, f"user-{i}", 1) for i in range(1, 8)])
        with sqlite3.connect(self.target) as db:
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT, active INTEGER)")
        self.mapping = [TableMapping("old_users", "users",
                                     {"id": "id", "display_name": "name", "active": "active"})]

    def tearDown(self):
        self.temp.cleanup()

    def test_explicit_value_transform_is_applied(self):
        mapping = [TableMapping("old_users", "users",
                                {"id": "id", "display_name": "name", "active": "active"},
                                {"name": "upper", "active": "bool"})]
        state = run_migration_batch(self.source, self.target, mapping,
                                    batch_id="batch-transform", batch_size=10)
        self.assertEqual(state["state"], "completed")
        with sqlite3.connect(self.target) as db:
            self.assertEqual(db.execute("SELECT name,active FROM users WHERE id=1").fetchone(),
                             ("USER-1", 1))

    def test_extract_transform_apply_and_diff(self):
        state = run_migration_batch(self.source, self.target, self.mapping,
                                     batch_id="batch-1", batch_size=3)
        self.assertEqual(state["state"], "completed")
        self.assertEqual(state["applied_rows"], 7)
        report = migration_diff_report(self.source, self.target, self.mapping, batch_id="batch-1")
        self.assertEqual(report["totals"]["missing_target_rows"], 0)
        self.assertEqual(report["totals"]["mismatched_rows"], 0)
        self.assertEqual(report["totals"]["matched_rows"], 7)

    def test_pause_and_resume_uses_persisted_cursor(self):
        paused = run_migration_batch(self.source, self.target, self.mapping,
                                     batch_id="batch-2", batch_size=2, max_chunks=1)
        self.assertEqual(paused["state"], "paused")
        self.assertEqual(paused["applied_rows"], 2)
        resumed = run_migration_batch(self.source, self.target, self.mapping,
                                      batch_id="batch-2", batch_size=2)
        self.assertEqual(resumed["state"], "completed")
        self.assertEqual(resumed["applied_rows"], 7)
        self.assertEqual(migration_batch_status(self.target, "batch-2")["state"], "completed")

    def test_completed_batch_is_idempotent(self):
        first = run_migration_batch(self.source, self.target, self.mapping,
                                    batch_id="batch-3", batch_size=2)
        second = run_migration_batch(self.source, self.target, self.mapping,
                                     batch_id="batch-3", batch_size=2)
        self.assertEqual(first["applied_rows"], second["applied_rows"])
        with sqlite3.connect(self.target) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM users").fetchone()[0], 7)

    def test_batch_id_cannot_be_reused_with_different_mapping(self):
        run_migration_batch(self.source, self.target, self.mapping,
                            batch_id="batch-4", batch_size=4, max_chunks=1)
        altered = [TableMapping("old_users", "users",
                                {"id": "id", "display_name": "active", "active": "name"})]
        with self.assertRaisesRegex(MigrationError, "different source or mapping"):
            run_migration_batch(self.source, self.target, altered, batch_id="batch-4")

    def test_diff_report_detects_missing_and_modified_rows_without_values(self):
        run_migration_batch(self.source, self.target, self.mapping, batch_id="batch-5")
        with sqlite3.connect(self.target) as db:
            db.execute("DELETE FROM users WHERE id=1")
            db.execute("UPDATE users SET name='tampered' WHERE id=2")
        report = migration_diff_report(self.source, self.target, self.mapping, batch_id="batch-5")
        self.assertEqual(report["totals"]["missing_target_rows"], 1)
        self.assertEqual(report["totals"]["mismatched_rows"], 1)
        self.assertFalse(report["values_included"])

    def test_rejects_unsafe_identifiers_and_composite_primary_keys(self):
        with self.assertRaises(ValueError):
            TableMapping("old_users; DROP TABLE users", "users", {"id": "id"})
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE composite(a INTEGER, b INTEGER, PRIMARY KEY(a,b))")
        with sqlite3.connect(self.target) as db:
            db.execute("CREATE TABLE composite_target(a INTEGER, b INTEGER, PRIMARY KEY(a,b))")
        with self.assertRaisesRegex(MigrationError, "exactly one primary-key"):
            run_migration_batch(self.source, self.target,
                                [TableMapping("composite", "composite_target",
                                              {"a": "a", "b": "b"})],
                                batch_id="batch-composite")

    def test_source_is_not_modified_and_missing_target_mapping_fails_closed(self):
        before = self.source.read_bytes()
        with self.assertRaises(MigrationError):
            run_migration_batch(self.source, self.target,
                                [TableMapping("old_users", "absent", {"id": "id"})],
                                batch_id="batch-7")
        self.assertEqual(self.source.read_bytes(), before)

    def test_diff_report_detects_extra_target_rows_and_requires_completed_batch(self):
        state = run_migration_batch(self.source, self.target, self.mapping,
                                    batch_id="batch-extra", batch_size=10)
        self.assertEqual(state["state"], "completed")
        with sqlite3.connect(self.target) as db:
            db.execute("INSERT INTO users VALUES(99, 'orphan-target-row', 1)")
        report = migration_diff_report(self.source, self.target, self.mapping,
                                       batch_id="batch-extra")
        table = report["tables"][0]
        self.assertEqual(table["source_rows"], 7)
        self.assertEqual(table["target_rows"], 8)
        self.assertEqual(table["extra_target_rows"], 1)
        self.assertEqual(table["missing_target_rows"], 0)
        self.assertEqual(table["mismatched_rows"], 0)
        self.assertFalse(table["consistent"])
        self.assertFalse(report["consistent"])
        self.assertLess(report["consistency_rate"], 1.0)

    def test_diff_report_marks_paused_batch_inconsistent_even_when_rows_match(self):
        run_migration_batch(self.source, self.target, self.mapping,
                            batch_id="batch-paused-diff", batch_size=10, max_chunks=1)
        report = migration_diff_report(self.source, self.target, self.mapping,
                                       batch_id="batch-paused-diff")
        self.assertEqual(report["totals"]["matched_rows"], 7)
        self.assertEqual(report["totals"]["extra_target_rows"], 0)
        self.assertFalse(report["consistent"])

    def test_missing_target_row_is_reported_after_partial_batch(self):
        paused = run_migration_batch(self.source, self.target, self.mapping,
                                     batch_id="batch-8", batch_size=2, max_chunks=1)
        report = migration_diff_report(self.source, self.target, self.mapping, batch_id="batch-8")
        self.assertEqual(report["totals"]["missing_target_rows"], 5)
        self.assertEqual(paused["state"], "paused")


if __name__ == "__main__":
    unittest.main()
