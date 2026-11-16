from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class InfrastructureReleaseScriptsTests(unittest.TestCase):
    def test_deploy_validates_config_snapshots_secrets_and_waits_for_health(self):
        script = (ROOT / "scripts" / "deploy_infrastructure.sh").read_text()
        for token in ("config --quiet", "chmod 600", "chmod 700", "pull", "up -d --wait", "env-"):
            with self.subTest(token=token):
                self.assertIn(token, script)

    def test_rollback_requires_release_snapshot_and_restores_env_on_failure(self):
        script = (ROOT / "scripts" / "rollback_infrastructure.sh").read_text()
        for token in ("ROLLBACK_SNAPSHOT", "deploy/releases", "config --quiet",
                      "up -d --wait", "pre-rollback-", "rollback failed"):
            with self.subTest(token=token):
                self.assertIn(token, script)

    def test_scripts_parse(self):
        for name in ("deploy_infrastructure.sh", "rollback_infrastructure.sh"):
            result = subprocess.run(
                ["bash", "-n", str(ROOT / "scripts" / name)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
