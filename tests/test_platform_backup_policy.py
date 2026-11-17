from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PlatformBackupPolicyTests(unittest.TestCase):
    def test_policy_defines_frequency_retention_encryption_and_integrity_for_every_component(self):
        policy = json.loads((ROOT / "deploy" / "backup-policy.json").read_text())
        self.assertEqual(set(policy["components"]), {"postgres", "redis", "objects", "config"})
        for component in policy["components"].values():
            self.assertTrue(component["frequency"])
            self.assertGreater(int(component["retention_days"]), 0)
            self.assertTrue(component["artifact_prefix"])
        self.assertEqual(policy["encryption"]["format"], "age")
        self.assertEqual(policy["integrity"]["algorithm"], "SHA-256")

    def test_orchestrator_covers_all_components_and_encrypted_config(self):
        script = (ROOT / "scripts" / "backup_platform.sh").read_text()
        for component in ("postgres", "redis", "objects", "config"):
            self.assertIn(f"{component})", script)
        self.assertIn("age --encrypt --recipient", script)
        self.assertIn("sha256sum", script)
        self.assertIn("manifest.json", script)
        self.assertIn("agent_platform/storage/postgres", script)
        self.assertIn("apply_retention", script)

    def test_retention_refuses_symlink_artifacts_and_removes_sidecars_together(self):
        script = (ROOT / "scripts" / "backup_platform.sh").read_text()
        self.assertIn("artifact.is_symlink()", script)
        self.assertIn("not sidecar.is_symlink()", script)
        self.assertIn("retention_days", script)

    def test_backup_orchestrator_shell_syntax(self):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / "backup_platform.sh")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
