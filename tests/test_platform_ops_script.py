import os
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "platform_ops.sh"


class PlatformOpsEntrypointTests(unittest.TestCase):
    def run_script(self, *args):
        env = os.environ.copy()
        env.pop("ROLLBACK_SNAPSHOT", None)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_help_is_available_without_infrastructure(self):
        result = self.run_script("help")
        self.assertEqual(result.returncode, 0)
        for command in ("deploy", "upgrade", "backup", "restore", "rollback", "verify", "diagnose"):
            self.assertIn(command, result.stdout)

    def test_unknown_command_fails_closed(self):
        result = self.run_script("not-a-command")
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown command", result.stderr)

    def test_restore_requires_known_component(self):
        result = self.run_script("restore", "unknown")
        self.assertEqual(result.returncode, 2)
        self.assertIn("restore requires", result.stderr)

    def test_upgrade_rejects_floating_latest_tag(self):
        compose = ROOT / "deploy" / "compose.yaml"
        if not compose.exists():
            self.skipTest("Compose definition is not present")
        # Static contract: the guard is evaluated before any Docker command.
        content = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("image:.*:latest", content)
        upgrade_index = content.index("upgrade)")
        guard_index = content.index("grep -Eq", upgrade_index)
        config_index = content.index("compose config --quiet", upgrade_index)
        self.assertLess(guard_index, config_index)


if __name__ == "__main__":
    unittest.main()
