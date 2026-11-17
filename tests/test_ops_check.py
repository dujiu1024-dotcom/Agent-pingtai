from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from scripts.ops_check import OpsCheckError, check_env_file, verify_backup


class OpsCheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def make_backup(self) -> Path:
        artifact = self.root / "platform-test.dump.age"
        artifact.write_bytes(b"encrypted-test-payload")
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        Path(f"{artifact}.sha256").write_text(f"{digest}  {artifact.name}\n", encoding="utf-8")
        Path(f"{artifact}.manifest.json").write_text(
            json.dumps({
                "format": "postgres-custom-age-v1",
                "artifact": artifact.name,
                "sha256": digest,
                "created_at_utc": "2026-11-17T00:00:00Z",
            }),
            encoding="utf-8",
        )
        return artifact

    def test_valid_backup_set_is_verified(self) -> None:
        result = verify_backup(self.make_backup())
        self.assertTrue(result["verified"])
        self.assertEqual(result["format"], "postgres-custom-age-v1")

    def test_checksum_mismatch_fails_closed(self) -> None:
        artifact = self.make_backup()
        artifact.write_bytes(b"tampered")
        with self.assertRaisesRegex(OpsCheckError, "checksum"):
            verify_backup(artifact)

    def test_manifest_filename_mismatch_fails_closed(self) -> None:
        artifact = self.make_backup()
        manifest_path = Path(f"{artifact}.manifest.json")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["artifact"] = "other.dump.age"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaisesRegex(OpsCheckError, "artifact name"):
            verify_backup(artifact)

    def test_missing_manifest_fails_closed(self) -> None:
        artifact = self.make_backup()
        Path(f"{artifact}.manifest.json").unlink()
        with self.assertRaisesRegex(OpsCheckError, "metadata"):
            verify_backup(artifact)

    def test_required_env_names_are_checked_without_exposing_values(self) -> None:
        env_file = self.root / ".env"
        env_file.write_text("PG_DSN=postgres://secret\nAGE_RECIPIENT=age1example\n", encoding="utf-8")
        self.assertEqual(check_env_file(env_file, ["PG_DSN", "AGE_RECIPIENT"]), ["AGE_RECIPIENT", "PG_DSN"])
        with self.assertRaisesRegex(OpsCheckError, "missing"):
            check_env_file(env_file, ["PG_DSN", "REDIS_PASSWORD"])


if __name__ == "__main__":
    unittest.main()
