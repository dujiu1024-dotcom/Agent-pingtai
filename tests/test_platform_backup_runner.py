from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.run_platform_backup import run_backup_set


class PlatformBackupRunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        (self.root / "deploy").mkdir()
        (self.root / "deploy" / "disaster-recovery-policy.json").write_text(
            json.dumps({"max_component_snapshot_skew_seconds": 3600}), encoding="utf-8",
        )
        for name in (
            "backup_postgres.sh",
            "backup_redis.sh",
            "backup_object_storage.sh",
            "backup_platform_config.sh",
        ):
            (self.root / "scripts" / name).write_text("#!/bin/sh\n", encoding="utf-8")
        self.backups = self.root / "backups"

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def _write_artifact(directory: Path, name: str, fmt: str, *, corrupt: bool = False, stamp: str):
        artifact = directory / name
        payload = f"encrypted fixture for {name}".encode()
        artifact.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        sidecar_digest = "0" * 64 if corrupt else digest
        Path(str(artifact) + ".sha256").write_text(
            f"{sidecar_digest}  {artifact.name}\n", encoding="utf-8",
        )
        Path(str(artifact) + ".manifest.json").write_text(json.dumps({
            "format": fmt,
            "artifact": artifact.name,
            "sha256": digest,
            "created_at_utc": stamp,
        }), encoding="utf-8")

    def test_all_four_components_create_fresh_verified_artifacts_and_set_manifest(self):
        artifacts = {
            "backup_postgres.sh": (
                "platform-20261117T010101Z.dump.age", "postgres-custom-age-v1",
                "2026-11-17T01:01:01Z",
            ),
            "backup_redis.sh": (
                "redis-20261117T010102Z.rdb.age", "redis-rdb-age-v1",
                "2026-11-17T01:01:02Z",
            ),
            "backup_object_storage.sh": (
                "object-storage-20261117T010103Z.tar.gz.age", "minio-mirror-age-v1",
                "2026-11-17T01:01:03Z",
            ),
            "backup_platform_config.sh": (
                "platform-config-20261117T010104Z.tar.gz.age", "platform-config-tar-age-v1",
                "2026-11-17T01:01:04Z",
            ),
        }

        def runner(command, *, check, env):
            self.assertTrue(check)
            self.assertEqual(env["BACKUP_DIR"], str(self.backups.resolve()))
            name = Path(command[-1]).name
            artifact, fmt, stamp = artifacts[name]
            self._write_artifact(self.backups, artifact, fmt, stamp=stamp)

        result = run_backup_set(self.backups, root=self.root, runner=runner)
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["components"], ["postgres", "redis", "object-storage", "config"])
        self.assertEqual(len(result["verified_artifacts"]), 4)
        self.assertEqual(result["snapshot_skew_seconds"], 3)
        manifest = json.loads(Path(result["manifest"]).read_text(encoding="utf-8"))
        self.assertEqual(manifest["component_count"], 4)
        self.assertEqual(manifest["status"], "verified")
        self.assertIn("not an atomic distributed snapshot", manifest["consistency"])
        self.assertEqual(Path(result["manifest"]).stat().st_mode & 0o777, 0o600)

    def test_missing_fresh_artifact_blocks_even_when_command_succeeds(self):
        def runner(_command, *, check, env):
            self.assertTrue(check)
            self.assertTrue(env["BACKUP_DIR"])

        with self.assertRaisesRegex(RuntimeError, "produced no new recognized artifact"):
            run_backup_set(self.backups, root=self.root, runner=runner)

    def test_corrupt_checksum_blocks_backup_set(self):
        def runner(command, *, check, env):
            self.assertTrue(check)
            if Path(command[-1]).name == "backup_postgres.sh":
                self._write_artifact(
                    self.backups,
                    "platform-20261117T020101Z.dump.age",
                    "postgres-custom-age-v1",
                    corrupt=True,
                    stamp="2026-11-17T02:01:01Z",
                )

        with self.assertRaisesRegex(RuntimeError, "artifact verification failed"):
            run_backup_set(self.backups, root=self.root, runner=runner)

    def test_component_command_failure_stops_pipeline(self):
        def runner(_command, *, check, env):
            raise subprocess.CalledProcessError(17, "backup")

        with self.assertRaisesRegex(RuntimeError, "postgres: backup command failed with exit code 17"):
            run_backup_set(self.backups, root=self.root, runner=runner)

    def test_backup_directory_symlink_is_rejected(self):
        target = self.root / "real-backups"
        target.mkdir()
        link = self.root / "backup-link"
        try:
            link.symlink_to(target, target_is_directory=True)
        except (NotImplementedError, OSError):
            self.skipTest("symlinks unavailable")
        with self.assertRaisesRegex(ValueError, "must not be a symlink"):
            run_backup_set(link, root=self.root, runner=lambda *_args, **_kwargs: None)

    def test_snapshot_skew_over_policy_blocks_the_set(self):
        def runner(command, *, check, env):
            name = Path(command[-1]).name
            entries = {
                "backup_postgres.sh": ("platform-20261117T010101Z.dump.age", "postgres-custom-age-v1", "2026-11-17T01:00:00Z"),
                "backup_redis.sh": ("redis-20261117T010102Z.rdb.age", "redis-rdb-age-v1", "2026-11-17T01:00:01Z"),
                "backup_object_storage.sh": ("object-storage-20261117T010103Z.tar.gz.age", "minio-mirror-age-v1", "2026-11-17T02:00:02Z"),
                "backup_platform_config.sh": ("platform-config-20261117T010104Z.tar.gz.age", "platform-config-tar-age-v1", "2026-11-17T01:00:02Z"),
            }
            artifact, fmt, stamp = entries[name]
            self._write_artifact(self.backups, artifact, fmt, stamp=stamp)

        with self.assertRaisesRegex(RuntimeError, "snapshot skew"):
            run_backup_set(self.backups, root=self.root, runner=runner)


if __name__ == "__main__":
    unittest.main()
