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


if __name__ == "__main__":
    unittest.main()
