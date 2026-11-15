"""Tests for safe, explicit legacy identity migration planning."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from agent_platform.identity.legacy_migration import (
    build_legacy_migration_plan,
    write_mapping_template,
)


class LegacyIdentityMigrationPlanTests(unittest.TestCase):
    def _database(self, path: Path) -> None:
        with sqlite3.connect(path) as connection:
            connection.execute(
                """CREATE TABLE users (
                   open_id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL,
                   projects TEXT NOT NULL DEFAULT '', console_token TEXT,
                   created_at TEXT NOT NULL)"""
            )
            connection.executemany(
                "INSERT INTO users VALUES (?, ?, ?, ?, ?, ?)",
                [
                    ("wx-user-1", "Alice", "member", "p1,p2", "never-export", "2025-01-01"),
                    ("wx-user-2", "Bob", "viewer", "p2", None, "2025-01-02"),
                ],
            )

    def test_template_contains_only_hashed_legacy_refs_and_requires_manual_claims(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            output = Path(directory) / "mapping.json"
            self._database(database)
            write_mapping_template(
                database, output, tenant_id="tenant-a", provider="wechat-work",
            )
            rendered = output.read_text(encoding="utf-8")
            self.assertNotIn("wx-user-1", rendered)
            self.assertNotIn("never-export", rendered)
            payload = json.loads(rendered)
            ref = hashlib.sha256(b"wx-user-1").hexdigest()[:16]
            self.assertEqual(payload["accounts"][ref]["user_id"], "")
            self.assertIn("member", payload["role_mappings"])

    def test_plan_requires_explicit_user_and_role_mappings(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            mapping = Path(directory) / "mapping.json"
            self._database(database)
            mapping.write_text(json.dumps({
                "mapping_version": 1, "tenant_id": "tenant-a",
                "provider": "wechat-work",
                "role_mappings": {
                    "member": {"tenant_role_id": "member", "project_role_id": None},
                    "viewer": {"tenant_role_id": None, "project_role_id": None},
                },
                "accounts": {
                    hashlib.sha256(b"wx-user-1").hexdigest()[:16]: {"user_id": str(uuid4())},
                    hashlib.sha256(b"wx-user-2").hexdigest()[:16]: {"user_id": str(uuid4())},
                },
            }), encoding="utf-8")
            plan = build_legacy_migration_plan(database, mapping)
            self.assertEqual(len(plan.accounts), 2)
            self.assertEqual(plan.accounts[0].project_ids, ("p1", "p2"))
            self.assertEqual(plan.accounts[0].tenant_role_id, "member")
            self.assertIsNone(plan.accounts[1].tenant_role_id)

    def test_missing_claim_or_role_mapping_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            mapping = Path(directory) / "mapping.json"
            self._database(database)
            mapping.write_text(json.dumps({
                "mapping_version": 1, "tenant_id": "tenant-a",
                "provider": "wechat-work", "role_mappings": {},
                "accounts": {},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "missing account claim"):
                build_legacy_migration_plan(database, mapping)

    def test_mapping_template_never_overwrites_existing_file(self):
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.sqlite"
            output = Path(directory) / "mapping.json"
            self._database(database)
            output.write_text("keep", encoding="utf-8")
            with self.assertRaises(FileExistsError):
                write_mapping_template(
                    database, output, tenant_id="tenant-a", provider="wechat-work",
                )
            self.assertEqual(output.read_text(encoding="utf-8"), "keep")


if __name__ == "__main__":
    unittest.main()
