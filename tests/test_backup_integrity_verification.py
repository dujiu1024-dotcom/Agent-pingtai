from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.verify_platform_backups import component_for, validate_tar, verify_artifact


class BackupIntegrityVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.artifact = self.root / "platform-20261117T000000Z.dump.age"
        self.payload = b"encrypted-payload-fixture"
        self.artifact.write_bytes(self.payload)
        digest = hashlib.sha256(self.payload).hexdigest()
        Path(str(self.artifact) + ".sha256").write_text(
            f"{digest}  {self.artifact.name}\n", encoding="utf-8"
        )
        Path(str(self.artifact) + ".manifest.json").write_text(json.dumps({
            "format": "postgres-custom-age-v1",
            "artifact": self.artifact.name,
            "sha256": digest,
        }), encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def test_config_prefix_is_not_misclassified_as_postgres(self):
        self.assertEqual(component_for(Path("platform-config-20261117.tar.gz.age")), "platform-config-tar-age-v1")

    def test_valid_checksum_and_manifest_pass(self):
        self.assertEqual(verify_artifact(self.artifact), [])

    def test_checksum_sidecar_mismatch_fails(self):
        Path(str(self.artifact) + ".sha256").write_text(
            f"{'0' * 64}  {self.artifact.name}\n", encoding="utf-8"
        )
        self.assertTrue(any("sidecar digest mismatch" in item for item in verify_artifact(self.artifact)))

    def test_manifest_digest_mismatch_fails(self):
        path = Path(str(self.artifact) + ".manifest.json")
        manifest = json.loads(path.read_text())
        manifest["sha256"] = "0" * 64
        path.write_text(json.dumps(manifest))
        self.assertTrue(any("manifest digest mismatch" in item for item in verify_artifact(self.artifact)))

    def test_manifest_artifact_and_format_are_bound_to_component(self):
        path = Path(str(self.artifact) + ".manifest.json")
        manifest = json.loads(path.read_text())
        manifest["artifact"] = "other.dump.age"
        manifest["format"] = "redis-rdb-age-v1"
        path.write_text(json.dumps(manifest))
        errors = verify_artifact(self.artifact)
        self.assertTrue(any("format mismatch" in item for item in errors))
        self.assertTrue(any("artifact name mismatch" in item for item in errors))

    def test_missing_sidecar_fails_closed(self):
        Path(str(self.artifact) + ".sha256").unlink()
        self.assertTrue(any("missing or unsafe" in item for item in verify_artifact(self.artifact)))

    def test_symlink_artifact_is_rejected(self):
        link = self.root / "platform-20261118T000000Z.dump.age"
        link.symlink_to(self.artifact)
        self.assertTrue(any("non-symlink" in item for item in verify_artifact(link)))

    def test_decrypt_invokes_age_and_postgres_format_validator(self):
        with patch("scripts.verify_platform_backups.subprocess.run") as run:
            run.return_value = None
            self.assertEqual(verify_artifact(self.artifact, decrypt=True, identity="/secure/age.key"), [])
            self.assertEqual(run.call_count, 2)
            self.assertEqual(run.call_args_list[0].args[0][0:2], ["age", "--decrypt"])
            self.assertEqual(run.call_args_list[1].args[0][0:2], ["pg_restore", "--list"])

    def test_tar_validation_rejects_path_traversal(self):
        import io
        import tarfile

        archive = self.root / "unsafe.tar.gz"
        with tarfile.open(archive, "w:gz") as output:
            info = tarfile.TarInfo("../escape")
            info.size = 1
            output.addfile(info, io.BytesIO(b"x"))
        with self.assertRaises(ValueError):
            validate_tar(archive)


if __name__ == "__main__":
    unittest.main()
