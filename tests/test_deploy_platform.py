from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from scripts.deploy_platform import deploy, initialize_env, parse_env, validate_env


class DeployPlatformTests(unittest.TestCase):
    def test_initialize_env_generates_distinct_secrets_and_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            example = root / ".env.example"
            target = root / ".env"
            example.write_text(
                "POSTGRES_DB=agent_platform\nPOSTGRES_USER=agent_platform\n"
                "POSTGRES_PASSWORD=REPLACE_WITH_RANDOM_POSTGRES_PASSWORD\n"
                "REDIS_PASSWORD=REPLACE_WITH_RANDOM_REDIS_PASSWORD\n"
                "MINIO_ROOT_USER=agent-platform\n"
                "MINIO_ROOT_PASSWORD=REPLACE_WITH_RANDOM_MINIO_PASSWORD\n",
                encoding="utf-8",
            )
            self.assertTrue(initialize_env(source=example, target=target))
            self.assertFalse(initialize_env(source=example, target=target))
            values = parse_env(target)
            self.assertNotEqual(values["POSTGRES_PASSWORD"], values["REDIS_PASSWORD"])
            self.assertNotIn("REPLACE_WITH_", target.read_text(encoding="utf-8"))
            if os.name == "posix":
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)

    def test_validation_rejects_missing_and_placeholder_secrets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "POSTGRES_DB=db\nPOSTGRES_USER=user\n"
                "POSTGRES_PASSWORD=REPLACE_WITH_SECRET\n"
                "REDIS_PASSWORD=secret\nMINIO_ROOT_USER=minio\n"
                "MINIO_ROOT_PASSWORD=secret\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "placeholder"):
                validate_env(path)

    def test_check_only_validates_compose_without_starting_services(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "POSTGRES_DB=db\nPOSTGRES_USER=user\nPOSTGRES_PASSWORD=pw\n"
                "REDIS_PASSWORD=redis\nMINIO_ROOT_USER=minio\nMINIO_ROOT_PASSWORD=minio-pw\n",
                encoding="utf-8",
            )
            # Exercise the command orchestration independently of Docker availability.
            runner = Mock()
            from unittest.mock import patch
            with patch("scripts.deploy_platform.ENV_FILE", env_file), patch(
                "scripts.deploy_platform.COMPOSE_FILE", Path(directory) / "compose.yaml"
            ):
                Path(directory, "compose.yaml").write_text("services: {}\n", encoding="utf-8")
                deploy(check_only=True, runner=runner)
            runner.assert_called_once()
            self.assertIn("config", runner.call_args.args[0])
            self.assertTrue(runner.call_args.kwargs["check"])

    def test_deploy_waits_for_health_before_reporting_success(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "POSTGRES_DB=db\nPOSTGRES_USER=user\nPOSTGRES_PASSWORD=pw\n"
                "REDIS_PASSWORD=redis\nMINIO_ROOT_USER=minio\nMINIO_ROOT_PASSWORD=minio-pw\n",
                encoding="utf-8",
            )
            compose = Path(directory) / "compose.yaml"
            compose.write_text("services: {}\n", encoding="utf-8")
            runner = Mock()
            from unittest.mock import patch
            with patch("scripts.deploy_platform.ENV_FILE", env_file), patch(
                "scripts.deploy_platform.COMPOSE_FILE", compose
            ):
                deploy(runner=runner)
            commands = [call.args[0] for call in runner.call_args_list]
            self.assertTrue(any("pull" in command for command in commands))
            self.assertTrue(any("--wait" in command for command in commands))
            self.assertTrue(any("ps" in command for command in commands))


if __name__ == "__main__":
    unittest.main()
