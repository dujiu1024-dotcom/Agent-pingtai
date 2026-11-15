from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class BackupRecoveryScriptTests(unittest.TestCase):
    def test_backup_requires_encryption_and_checksum(self):
        script = (ROOT / "scripts" / "backup_postgres.sh").read_text()
        self.assertIn("PG_DSN", script)
        self.assertIn("AGE_RECIPIENT", script)
        self.assertIn("age --encrypt", script)
        self.assertIn("sha256sum", script)
        self.assertIn("chmod 700", script)

    def test_restore_requires_explicit_confirmation_and_checksum(self):
        script = (ROOT / "scripts" / "restore_postgres.sh").read_text()
        self.assertIn("AGE_IDENTITY", script)
        self.assertIn("CONFIRM_RESTORE", script)
        self.assertIn("RESTORE-PLATFORM-DATABASE", script)
        self.assertIn("sha256sum --check", script)
        self.assertIn("age --decrypt", script)
        self.assertIn("pg_restore --dbname", script)



    def test_backup_manifest_and_restore_safety_contract(self):
        backup = (ROOT / "scripts" / "backup_postgres.sh").read_text()
        restore = (ROOT / "scripts" / "restore_postgres.sh").read_text()
        self.assertIn("manifest.json", backup)
        self.assertIn("pg_dump --version", backup)
        self.assertIn('mv "$MANIFEST_TMP"', backup)
        self.assertIn("manifest checksum mismatch", restore)
        self.assertIn("pg_restore --list", restore)
        self.assertIn("--single-transaction", restore)
        self.assertIn("ALLOW_DIFFERENT_DATABASE", restore)

    def test_migration_runner_is_checksummed_and_serialized(self):
        script = (ROOT / "scripts" / "migrate_postgres.sh").read_text()
        self.assertIn("pg_advisory_lock", script)
        self.assertIn("platform_schema_migrations", script)
        self.assertIn("checksum_matches", script)
        self.assertIn("ON_ERROR_STOP", script)

if __name__ == "__main__":
    unittest.main()
