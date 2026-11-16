from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ObjectStorageBackupTests(unittest.TestCase):
    def test_backup_requires_external_credentials_and_encryption(self):
        script = (ROOT / "scripts" / "backup_object_storage.sh").read_text()
        for token in (
            "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY",
            "MINIO_BUCKET", "AGE_RECIPIENT", "mc mirror --preserve",
            "age --encrypt", "sha256sum", "chmod 700", "chmod 600",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)

    def test_backup_does_not_claim_to_preserve_object_versions(self):
        script = (ROOT / "scripts" / "backup_object_storage.sh").read_text()
        self.assertIn("does not preserve historical object versions", script)

    def test_backup_script_parses(self):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / "backup_object_storage.sh")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
