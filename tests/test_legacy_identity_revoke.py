"""Tests for explicit backup-first legacy Console token invalidation."""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.identity.legacy_revoke import (
    CONFIRMATION,
    invalidate_legacy_console_tokens,
)


class LegacyIdentityRevokeTests(unittest.TestCase):
    def _database(self, path):
        with sqlite3.connect(path) as connection:
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
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("open-a", "Alice", "admin", "p1", "token-a", "2025-01-01"),
                    ("open-b", "Bob", "member", "", "token-b", "2025-01-02"),
                    ("open-c", "Carol", "member", "", None, "2025-01-03"),
                ],
            )

    def test_requires_explicit_confirmation_before_any_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.db"
            backup = Path(directory) / "backup.db"
            self._database(database)
            before = database.read_bytes()
            with self.assertRaises(ValueError):
                invalidate_legacy_console_tokens(
                    database, backup, confirmation="yes",
                )
            self.assertEqual(database.read_bytes(), before)
            self.assertFalse(backup.exists())

    def test_backup_is_created_before_all_legacy_tokens_are_cleared(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.db"
            backup = Path(directory) / "backup.db"
            self._database(database)
            result = invalidate_legacy_console_tokens(
                database, backup, confirmation=CONFIRMATION,
            )
            self.assertEqual(result["revoked_token_count"], 2)
            self.assertEqual(result["remaining_legacy_tokens"], 0)
            with sqlite3.connect(database) as connection:
                remaining = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE console_token IS NOT NULL"
                ).fetchone()[0]
            with sqlite3.connect(backup) as connection:
                preserved = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE console_token IS NOT NULL"
                ).fetchone()[0]
            self.assertEqual(remaining, 0)
            self.assertEqual(preserved, 2)

    def test_existing_backup_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.db"
            backup = Path(directory) / "backup.db"
            self._database(database)
            backup.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                invalidate_legacy_console_tokens(
                    database, backup, confirmation=CONFIRMATION,
                )
            self.assertEqual(backup.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
