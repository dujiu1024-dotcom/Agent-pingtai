from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_platform.data_governance.legacy_inventory import (
    DEFAULT_LEGACY_ASSETS,
    LegacyAsset,
    MigrationDecision,
    build_legacy_inventory,
    inventory_report,
    validate_inventory,
)


class LegacyInventoryTests(unittest.TestCase):
    def test_catalog_is_sorted_and_requires_explicit_targets(self):
        records = validate_inventory(DEFAULT_LEGACY_ASSETS)
        self.assertEqual(len(records), 7)
        self.assertEqual(records, tuple(sorted(records, key=lambda item: item.name)))
        self.assertTrue(all(item.target for item in records))
        self.assertEqual(inventory_report()["mapping_candidates"], 7)
        with self.assertRaises(ValueError):
            LegacyAsset("db", "db.py", MigrationDecision.ADAPT, "data")
        with self.assertRaises(ValueError):
            validate_inventory((records[0], records[0]))

    def test_scans_sqlite_schema_counts_identity_and_business_without_row_values(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "agency").mkdir()
            (root / "agency.py").write_text("# legacy runtime\n", encoding="utf-8")
            db = root / "agency" / "state.db"
            connection = sqlite3.connect(db)
            connection.executescript("""
                CREATE TABLE users(id INTEGER PRIMARY KEY, open_id TEXT, console_token TEXT);
                CREATE TABLE projects(id INTEGER PRIMARY KEY, owner_user_id INTEGER REFERENCES users(id), name TEXT);
                INSERT INTO users VALUES(1, 'raw-open-id', 'do-not-export-this-token');
                INSERT INTO projects VALUES(2, 1, 'secret-customer-project');
            """)
            connection.commit()
            connection.close()
            report = build_legacy_inventory(root, db)
            self.assertEqual(report["database"]["status"], "scanned")
            self.assertEqual(report["database"]["total_rows"], 2)
            users = next(item for item in report["database"]["tables"] if item["name"] == "users")
            self.assertEqual(users["rows"], 1)
            self.assertIn("console_token", users["sensitive_columns"])
            self.assertTrue(report["identity_relations"])
            self.assertFalse(report["secrets_or_row_values_exported"])
            rendered = json.dumps(report)
            self.assertNotIn("raw-open-id", rendered)
            self.assertNotIn("do-not-export-this-token", rendered)
            self.assertNotIn("secret-customer-project", rendered)

    def test_missing_database_is_unknown_not_zero_and_file_scan_is_metadata_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "db.py").write_text("# old db adapter", encoding="utf-8")
            report = build_legacy_inventory(root)
            self.assertEqual(report["database"]["status"], "not_found")
            self.assertIsNone(report["database"]["total_rows"])
            self.assertEqual(report["files"]["count"], 1)
            self.assertEqual(report["files"]["items"][0]["inspection"], "filesystem_metadata_only")

    def test_rejects_symlinked_database_and_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real"
            real.mkdir()
            db = real / "legacy.db"
            sqlite3.connect(db).close()
            db_link = root / "db-link"
            root_link = root / "root-link"
            try:
                db_link.symlink_to(db)
                root_link.symlink_to(real, target_is_directory=True)
            except (NotImplementedError, OSError):
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(ValueError, "database must be a regular"):
                build_legacy_inventory(real, db_link)
            with self.assertRaisesRegex(ValueError, "root must be a real"):
                build_legacy_inventory(root_link)


if __name__ == "__main__":
    unittest.main()
