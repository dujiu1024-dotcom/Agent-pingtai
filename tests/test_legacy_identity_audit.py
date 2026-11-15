"""Tests for the read-only legacy SQLite identity inventory."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.identity.legacy_audit import audit_legacy_users


class LegacyIdentityAuditTests(unittest.TestCase):
    def test_report_is_read_only_and_never_exports_raw_tokens_or_open_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.db"
            with sqlite3.connect(database) as connection:
                connection.execute(
                    """CREATE TABLE users (
                       open_id TEXT PRIMARY KEY,
                       name TEXT NOT NULL,
                       role TEXT NOT NULL,
                       projects TEXT NOT NULL DEFAULT '',
                       console_token TEXT UNIQUE,
                       created_at TEXT NOT NULL
                    )"""
                )
                connection.executemany(
                    """INSERT INTO users
                       (open_id, name, role, projects, console_token, created_at)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    [
                        ("wx-open-id-secret", "Alice", "admin", "p1,p2,p1",
                         "console-secret-token", "2025-01-01"),
                        ("wx-open-id-2", "Bob", "operator", "",
                         None, "2025-02-01"),
                    ],
                )
            before = database.read_bytes()
            report = audit_legacy_users(database)
            after = database.read_bytes()

            self.assertEqual(before, after)
            self.assertFalse(report["source_mutated"])
            self.assertEqual(report["summary"]["users_total"], 2)
            self.assertEqual(report["summary"]["project_memberships_total"], 2)
            self.assertEqual(report["summary"]["users_with_legacy_console_token"], 1)
            self.assertEqual(report["records"][0]["project_ids"], ["p1", "p2"])
            self.assertTrue(report["records"][0]["legacy_console_token_must_be_invalidated"])
            rendered = json.dumps(report)
            self.assertNotIn("console-secret-token", rendered)
            self.assertNotIn("wx-open-id-secret", rendered)

    def test_missing_users_table_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "empty.db"
            sqlite3.connect(database).close()
            with self.assertRaisesRegex(ValueError, "users table"):
                audit_legacy_users(database)

    def test_missing_legacy_columns_are_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "partial.db"
            with sqlite3.connect(database) as connection:
                connection.execute("CREATE TABLE users (open_id TEXT)")
            with self.assertRaisesRegex(ValueError, "missing required columns"):
                audit_legacy_users(database)


if __name__ == "__main__":
    unittest.main()
