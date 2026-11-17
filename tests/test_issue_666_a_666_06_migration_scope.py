from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.data_governance.migration_batches import (
    MigrationError,
    TableMapping,
    validate_migration_scope,
)


class MigrationScopeCoverageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.source = root / "source.db"
        self.target = root / "target.db"
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
            db.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY, title TEXT)")
            db.execute("CREATE TABLE audit(id INTEGER PRIMARY KEY, event TEXT)")
        with sqlite3.connect(self.target) as db:
            db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
            db.execute("CREATE TABLE sessions(id INTEGER PRIMARY KEY, title TEXT)")
            db.execute("CREATE TABLE audit(id INTEGER PRIMARY KEY, event TEXT)")

    def tearDown(self):
        self.temp.cleanup()

    def mappings(self):
        return [
            TableMapping("users", "users", {"id": "id", "name": "name"}),
            TableMapping("sessions", "sessions", {"id": "id", "title": "title"}),
            TableMapping("audit", "audit", {"id": "id", "event": "event"}),
        ]

    def test_full_source_inventory_is_required_for_100_percent_claim(self):
        report = validate_migration_scope(
            self.source, self.target, self.mappings()
        )
        self.assertTrue(report["scope_accounted"])
        self.assertTrue(report["eligible_for_100_percent_consistency"])
        self.assertEqual(report["source_table_count"], 3)
        self.assertEqual(report["mapped_table_count"], 3)

    def test_omitted_source_table_fails_closed(self):
        with self.assertRaisesRegex(MigrationError, "unaccounted source tables: audit"):
            validate_migration_scope(
                self.source, self.target, self.mappings()[:2]
            )

    def test_excluded_table_requires_rationale_and_blocks_full_consistency(self):
        mappings = self.mappings()[:2]
        with self.assertRaisesRegex(MigrationError, "requires a rationale"):
            validate_migration_scope(
                self.source, self.target, mappings, excluded_source_tables={"audit": " "}
            )
        report = validate_migration_scope(
            self.source, self.target, mappings,
            excluded_source_tables={"audit": "legacy transient audit table excluded by approved scope"},
        )
        self.assertTrue(report["scope_accounted"])
        self.assertFalse(report["eligible_for_100_percent_consistency"])
        self.assertEqual(report["excluded_table_count"], 1)

    def test_unknown_source_and_missing_target_tables_fail_closed(self):
        with self.assertRaisesRegex(MigrationError, "unknown source tables"):
            validate_migration_scope(
                self.source, self.target,
                [TableMapping("ghost", "users", {"id": "id"})],
            )
        with self.assertRaisesRegex(MigrationError, "missing target tables"):
            validate_migration_scope(
                self.source, self.target,
                [
                    TableMapping("users", "users", {"id": "id", "name": "name"}),
                    TableMapping("sessions", "sessions", {"id": "id", "title": "title"}),
                    TableMapping("audit", "missing_audit", {"id": "id", "event": "event"}),
                ],
            )

    def test_duplicate_source_or_target_mappings_are_rejected(self):
        with self.assertRaisesRegex(MigrationError, "source tables must be mapped exactly once"):
            validate_migration_scope(
                self.source, self.target,
                self.mappings() + [TableMapping("users", "users", {"id": "id"})],
            )
        duplicate_target = [
            TableMapping("users", "users", {"id": "id", "name": "name"}),
            TableMapping("sessions", "users", {"id": "id", "title": "title"}),
            self.mappings()[2],
        ]
        with self.assertRaisesRegex(MigrationError, "target tables must be mapped exactly once"):
            validate_migration_scope(self.source, self.target, duplicate_target)


if __name__ == "__main__":
    unittest.main()
