from __future__ import annotations
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class ReleaseUpgradeTests(unittest.TestCase):
    def test_scripts_parse(self):
        for name in ("release_upgrade.sh", "check_migrations.sh"):
            result = subprocess.run(["bash", "-n", str(ROOT / "scripts" / name)], capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_upgrade_contract_contains_safety_gates(self):
        script = (ROOT / "scripts" / "release_upgrade.sh").read_text()
        for token in ("RELEASE_COMPATIBILITY_MIN", "RELEASE_COMPATIBILITY_MAX", "PRE_UPGRADE_BACKUP_COMMAND", "MIGRATION_COMMAND", "up -d --no-deps --wait", "restore_previous", "database migrations were not reversed"):
            with self.subTest(token=token):
                self.assertIn(token, script)

    def test_migration_preflight_is_read_only_and_checks_checksums(self):
        script = (ROOT / "scripts" / "check_migrations.sh").read_text()
        self.assertIn("to_regclass", script)
        self.assertIn("sha256sum", script)
        self.assertIn("checksum mismatch", script)
        self.assertNotIn("INSERT INTO", script)
        self.assertNotIn("CREATE TABLE", script)

    def test_incompatible_release_is_blocked_before_state_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "release.env"
            env_file.write_text("SAFE_VALUE=unchanged\n", encoding="utf-8")
            compose = root / "compose.yaml"
            compose.write_text("services:\n  api:\n    image: example/api:1.2.0\n", encoding="utf-8")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log = root / "docker.log"
            docker = bin_dir / "docker"
            docker.write_text("#!/usr/bin/env bash\nprintf \"%s\\n\" \"$*\" >> \"$DOCKER_LOG\"\nif [[ \"$*\" == *\" config --services\" ]]; then echo api; fi\n", encoding="utf-8")
            docker.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "RELEASE_ENV_FILE": str(env_file), "RELEASE_COMPOSE_FILE": str(compose),
                "RELEASE_STATE_DIR": str(root / "state"), "DOCKER_LOG": str(log),
                "PATH": f"{bin_dir}:{env['PATH']}", "CURRENT_RELEASE_VERSION": "1.0.0",
                "RELEASE_VERSION": "2.0.0", "RELEASE_COMPATIBILITY_MIN": "1.5.0",
                "RELEASE_COMPATIBILITY_MAX": "2.0.0", "UPGRADE_SERVICES": "api",
            })
            result = subprocess.run(["bash", str(ROOT / "scripts" / "release_upgrade.sh"), "--check"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 3)
            self.assertIn("outside target compatibility window", result.stderr)
            self.assertFalse((root / "state").exists())
            self.assertEqual(env_file.read_text(encoding="utf-8"), "SAFE_VALUE=unchanged\n")

if __name__ == "__main__":
    unittest.main()
