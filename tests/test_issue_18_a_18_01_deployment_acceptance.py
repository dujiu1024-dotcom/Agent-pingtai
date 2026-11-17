from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class Issue18DeploymentAcceptanceTests(unittest.TestCase):
    def test_empty_environment_bootstrap_has_documented_production_entrypoint(self):
        operations = (ROOT / "deploy" / "OPERATIONS.md").read_text(encoding="utf-8")
        compose = yaml.safe_load((ROOT / "deploy" / "production.compose.yaml").read_text(encoding="utf-8"))
        self.assertIn("First deployment", operations)
        self.assertIn("deploy/.env.production.example", operations)
        self.assertIn("docker compose", operations)
        self.assertIn("platform-api", compose["services"])
        self.assertIn("platform-worker", compose["services"])
        self.assertFalse((ROOT / "deploy" / ".env.production").exists())

    def test_release_upgrade_shell_syntax_is_valid(self):
        result = subprocess.run(
            ["bash", "-n", str(ROOT / "scripts" / "release_upgrade.sh")],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_failed_upgrade_restores_previous_success_not_failed_target(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "release.env"
            prior_env = (
                "CURRENT_RELEASE_VERSION=1.0.0\nRELEASE_VERSION=1.0.0\n"
                "RELEASE_COMPATIBILITY_MIN=1.0.0\nRELEASE_COMPATIBILITY_MAX=1.0.0\n"
                "UPGRADE_SERVICES=api\nPG_DSN=postgresql://test:test@localhost/test\n"
            )
            target_env = (
                "CURRENT_RELEASE_VERSION=1.0.0\nRELEASE_VERSION=1.1.0\n"
                "RELEASE_COMPATIBILITY_MIN=1.0.0\nRELEASE_COMPATIBILITY_MAX=1.1.0\n"
                "UPGRADE_SERVICES=api\nPG_DSN=postgresql://test:test@localhost/test\n"
            )
            env_file.write_text(target_env, encoding="utf-8")
            state_dir = root / "state"
            state_dir.mkdir()
            prior_snapshot = state_dir / "release-env-known-good"
            prior_snapshot.write_text(prior_env, encoding="utf-8")
            (state_dir / "latest-success").write_text(str(prior_snapshot) + "\n", encoding="utf-8")

            bin_dir = root / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                'case "$*" in\n'
                '  *" config --services") printf "api\\n" ;;\n'
                '  *" up -d --no-deps --wait api")\n'
                '    count=0; [[ ! -f "$UP_COUNT" ]] || read -r count < "$UP_COUNT"\n'
                '    count=$((count + 1)); printf "%s\\n" "$count" > "$UP_COUNT"\n'
                '    if [[ "$count" == "1" ]]; then exit 42; fi ;;\n'
                "esac\n",
                encoding="utf-8",
            )
            docker.chmod(0o755)
            psql = bin_dir / "psql"
            psql.write_text("#!/usr/bin/env bash\necho f\n", encoding="utf-8")
            psql.chmod(0o755)

            env = os.environ.copy()
            env.update({
                "RELEASE_ENV_FILE": str(env_file),
                "RELEASE_COMPOSE_FILE": str(ROOT / "deploy" / "production.compose.yaml"),
                "RELEASE_STATE_DIR": str(state_dir),
                "UP_COUNT": str(root / "up-count"),
                "PRE_UPGRADE_BACKUP_COMMAND": "true",
                "PATH": f"{bin_dir}:{env['PATH']}",
            })
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "release_upgrade.sh"), "--apply"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertIn("restoring prior configuration", result.stderr)
            self.assertEqual(env_file.read_text(encoding="utf-8"), prior_env)
            self.assertEqual(
                (state_dir / "latest-success").read_text(encoding="utf-8"),
                str(prior_snapshot) + "\n",
            )
            self.assertFalse((state_dir / "previous-success").exists())

    def test_upgrade_fails_closed_without_known_good_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            env_file = root / "release.env"
            env_file.write_text(
                "CURRENT_RELEASE_VERSION=1.0.0\nRELEASE_VERSION=1.1.0\n"
                "RELEASE_COMPATIBILITY_MIN=1.0.0\nRELEASE_COMPATIBILITY_MAX=1.1.0\n"
                "UPGRADE_SERVICES=api\nPG_DSN=postgresql://test:test@localhost/test\n",
                encoding="utf-8",
            )
            bin_dir = root / "bin"
            bin_dir.mkdir()
            docker = bin_dir / "docker"
            docker.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            docker.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "RELEASE_ENV_FILE": str(env_file),
                "RELEASE_COMPOSE_FILE": str(ROOT / "deploy" / "production.compose.yaml"),
                "RELEASE_STATE_DIR": str(root / "state"),
                "PRE_UPGRADE_BACKUP_COMMAND": "true",
                "PATH": f"{bin_dir}:{env['PATH']}",
            })
            result = subprocess.run(
                ["bash", str(ROOT / "scripts" / "release_upgrade.sh"), "--apply"],
                cwd=ROOT, env=env, text=True, capture_output=True, check=False,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("snapshot pointer is missing", result.stderr)
            self.assertFalse((root / "state" / "latest-success").exists())


if __name__ == "__main__":
    unittest.main()
