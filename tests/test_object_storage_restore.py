from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ObjectStorageRestoreTests(unittest.TestCase):
    def test_restore_requires_confirmation_and_validates_manifest(self):
        script = (ROOT / "scripts" / "restore_object_storage.sh").read_text()
        for token in (
            "CONFIRM_OBJECT_RESTORE", "RESTORE-PLATFORM-OBJECTS",
            "sha256sum --check", "minio-mirror-age-v1",
            "manifest checksum mismatch", "age --decrypt",
            "unsafe archive path", "issym()", "islnk()",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)

    def test_deleting_extra_destination_objects_is_explicit_opt_in(self):
        script = (ROOT / "scripts" / "restore_object_storage.sh").read_text()
        self.assertIn('OBJECT_RESTORE_DELETE_EXTRA:-0', script)
        self.assertIn("mc mirror --preserve --overwrite --remove", script)
        self.assertIn("mc mirror --preserve --overwrite", script)

    def test_restore_script_parses(self):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / "restore_object_storage.sh")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
