from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class RedisBackupRestoreTests(unittest.TestCase):
    def test_backup_uses_authenticated_rdb_snapshot_and_encryption(self):
        script = (ROOT / "scripts" / "backup_redis.sh").read_text()
        for token in (
            "REDIS_PASSWORD", "REDISCLI_AUTH", "redis-cli",
            "--rdb", "age --encrypt", "sha256sum", "redis-rdb-age-v1",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)

    def test_restore_is_confirmed_validated_and_preserves_previous_rdb(self):
        script = (ROOT / "scripts" / "restore_redis.sh").read_text()
        for token in (
            "RESTORE-PLATFORM-REDIS", "sha256sum --check",
            "redis-check-rdb", "age --decrypt", ".pre-restore.",
            "refusing to replace symlink RDB", "REDIS_DATA_DIR",
        ):
            with self.subTest(token=token):
                self.assertIn(token, script)

    def test_scripts_parse(self):
        for name in ("backup_redis.sh", "restore_redis.sh"):
            result = subprocess.run(
                ["bash", "-n", str(ROOT / "scripts" / name)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
