from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from scripts.validate_migrations import validate_migrations


class ValidateMigrationsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def add_migration(self, name: str, content: str = "SELECT 1;") -> Path:
        path = self.directory / name
        path.write_text(content, encoding="utf-8")
        checksum = hashlib.sha256(path.read_bytes()).hexdigest()
        path.with_name(f"{path.name}.sha256").write_text(
            f"{checksum}  {path.name}\n", encoding="ascii"
        )
        return path

    def test_accepts_unique_numbers_with_matching_sha256_sidecars(self):
        self.add_migration("001_create_users.sql")
        self.add_migration("002_add_email.sql")
        report = validate_migrations(self.directory)
        self.assertTrue(report["valid"])
        self.assertEqual(report["migration_count"], 2)
        self.assertEqual(report["verified_checksum_count"], 2)
        self.assertEqual(report["issues"], [])

    def test_detects_duplicate_numeric_prefixes(self):
        self.add_migration("007_create_users.sql")
        self.add_migration("007_add_email.sql")
        report = validate_migrations(self.directory)
        self.assertFalse(report["valid"])
        self.assertIn("duplicate_number", {item["code"] for item in report["issues"]})

    def test_detects_missing_checksum_sidecar(self):
        (self.directory / "003_add_index.sql").write_text("SELECT 1;", encoding="utf-8")
        report = validate_migrations(self.directory)
        self.assertIn("missing_checksum", {item["code"] for item in report["issues"]})

    def test_detects_malformed_and_mismatched_checksums(self):
        migration = self.add_migration("004_add_column.sql")
        sidecar = migration.with_name(f"{migration.name}.sha256")
        sidecar.write_text("not-a-sha256\n", encoding="ascii")
        malformed = validate_migrations(self.directory)
        self.assertIn("invalid_checksum", {item["code"] for item in malformed["issues"]})

        sidecar.write_text(f"{'0' * 64}  {migration.name}\n", encoding="ascii")
        mismatched = validate_migrations(self.directory)
        self.assertIn("checksum_mismatch", {item["code"] for item in mismatched["issues"]})

    def test_rejects_missing_directory_and_empty_directory(self):
        with self.assertRaisesRegex(ValueError, "does not exist"):
            validate_migrations(self.directory / "missing")
        report = validate_migrations(self.directory)
        self.assertIn("no_migrations", {item["code"] for item in report["issues"]})

    def test_reports_invalid_migration_filename(self):
        (self.directory / "README.sql").write_text("SELECT 1;", encoding="utf-8")
        report = validate_migrations(self.directory)
        self.assertIn("invalid_filename", {item["code"] for item in report["issues"]})


if __name__ == "__main__":
    unittest.main()
