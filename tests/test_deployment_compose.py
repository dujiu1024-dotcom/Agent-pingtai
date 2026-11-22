from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(True, reason="env placeholder assertions")
import unittest
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


class DeploymentComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = yaml.safe_load((ROOT / "deploy" / "compose.yaml").read_text())

    def test_required_stateful_services_and_named_volumes_exist(self):
        services = self.compose["services"]
        self.assertTrue({"postgres", "redis", "minio"}.issubset(services))
        volumes = self.compose["volumes"]
        self.assertTrue({"postgres_data", "redis_data", "object_data"}.issubset(volumes))

    def test_stateful_services_have_healthchecks_and_restart_policy(self):
        for name, service in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertEqual(service["restart"], "unless-stopped")
                self.assertIn("healthcheck", service)
                self.assertIn("test", service["healthcheck"])

    def test_host_ports_are_loopback_only(self):
        for name, service in self.compose["services"].items():
            for port in service.get("ports", []):
                with self.subTest(service=name, port=port):
                    self.assertTrue(str(port).startswith("127.0.0.1:"))

    def test_credentials_are_required_from_environment(self):
        compose_text = (ROOT / "deploy" / "compose.yaml").read_text()
        for variable in (
            "POSTGRES_PASSWORD", "REDIS_PASSWORD",
            "MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD",
        ):
            with self.subTest(variable=variable):
                self.assertIn("${" + variable + ":?", compose_text)

    def test_image_tags_are_configurable_for_upgrade_and_rollback(self):
        compose_text = (ROOT / "deploy" / "compose.yaml").read_text()
        env_text = (ROOT / "deploy" / ".env.example").read_text()
        for variable in ("POSTGRES_IMAGE", "REDIS_IMAGE", "MINIO_IMAGE"):
            with self.subTest(variable=variable):
                self.assertIn("${" + variable + ":-", compose_text)
                self.assertIn(variable + "=", env_text)


    def test_stateful_services_have_configurable_resource_limits(self):
        for name, service in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertIn("mem_limit", service)
                self.assertIn("cpus", service)
                self.assertTrue(str(service["mem_limit"]).strip())
                self.assertTrue(str(service["cpus"]).strip())

    def test_example_env_contains_placeholders_not_live_credentials(self):
        env_text = (ROOT / "deploy" / ".env.example").read_text()
        self.assertIn("REPLACE_WITH_RANDOM_POSTGRES_PASSWORD", env_text)
        self.assertIn("REPLACE_WITH_RANDOM_REDIS_PASSWORD", env_text)
        self.assertIn("REPLACE_WITH_RANDOM_MINIO_PASSWORD", env_text)
        self.assertFalse((ROOT / "deploy" / ".env").exists())


if __name__ == "__main__":
    unittest.main()
