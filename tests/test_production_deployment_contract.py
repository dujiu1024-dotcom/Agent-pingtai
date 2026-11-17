from __future__ import annotations

import unittest
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.health import create_health_router


class HealthProbeTests(unittest.TestCase):
    def test_liveness_is_independent_of_dependencies(self):
        app = FastAPI()
        app.include_router(create_health_router(readiness_checks={"db": lambda: False}))
        client = TestClient(app)
        self.assertEqual(client.get("/health/live").status_code, 200)
        self.assertEqual(client.get("/health/ready").status_code, 503)

    def test_readiness_supports_async_checks_and_reports_failure_without_leaking_errors(self):
        async def healthy():
            return True

        def unavailable():
            raise RuntimeError("secret connection string must not be exposed")

        app = FastAPI()
        app.include_router(create_health_router(
            readiness_checks={"postgres": healthy, "redis": unavailable},
        ))
        response = TestClient(app).get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["checks"], {
            "postgres": "ok", "redis": "unavailable",
        })
        self.assertNotIn("secret connection string", response.text)

    def test_empty_readiness_configuration_fails_closed(self):
        app = FastAPI()
        app.include_router(create_health_router(readiness_checks={}))
        response = TestClient(app).get("/health/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")


class ProductionDeploymentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compose = yaml.safe_load(
            Path("deploy/production.compose.yaml").read_text(encoding="utf-8")
        )

    def test_runtime_waits_for_all_dependencies_and_uses_readiness_probe(self):
        api = self.compose["services"]["platform-api"]
        self.assertEqual(set(api["depends_on"]), {"postgres", "redis", "minio"})
        self.assertTrue(all(
            item["condition"] == "service_healthy"
            for item in api["depends_on"].values()
        ))
        self.assertIn("/health/ready", " ".join(api["healthcheck"]["test"]))

    def test_services_are_loopback_published_and_resource_limited(self):
        for name, service in self.compose["services"].items():
            with self.subTest(service=name):
                self.assertTrue(service.get("mem_limit"))
                self.assertTrue(service.get("cpus"))
                for port in service.get("ports", []):
                    self.assertTrue(str(port).startswith("127.0.0.1:"))

    def test_runtime_is_hardened_and_secrets_are_file_mounted(self):
        api = self.compose["services"]["platform-api"]
        self.assertTrue(api["read_only"])
        self.assertEqual(api["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", api["security_opt"])
        self.assertEqual(set(api["secrets"]), {
            "postgres_password", "redis_password", "minio_root_password",
        })
        self.assertEqual(set(self.compose["secrets"]), {
            "postgres_password", "redis_password", "minio_root_password",
        })

    def test_worker_is_separate_hardened_and_uses_same_immutable_image(self):
        services = self.compose["services"]
        api = services["platform-api"]
        worker = services["platform-worker"]
        self.assertEqual(worker["image"], api["image"])
        self.assertIn("python -m", " ".join(worker["command"]))
        self.assertIn("${PLATFORM_WORKER_MODULE}", " ".join(worker["command"]))
        self.assertIn("PLATFORM_WORKER_MODULE", worker["environment"])
        self.assertIn("WORKER_CONCURRENCY", worker["environment"])
        self.assertEqual(set(worker["depends_on"]), {"postgres", "redis", "minio"})
        self.assertTrue(all(
            item["condition"] == "service_healthy"
            for item in worker["depends_on"].values()
        ))
        self.assertNotIn("ports", worker)
        self.assertEqual(worker["secrets"], api["secrets"])
        self.assertEqual(worker["user"], "10001:10001")
        self.assertTrue(worker["read_only"])
        self.assertEqual(worker["cap_drop"], ["ALL"])
        self.assertIn("no-new-privileges:true", worker["security_opt"])
        self.assertEqual(worker["stop_grace_period"], "60s")
        self.assertTrue(worker["mem_limit"])
        self.assertTrue(worker["cpus"])
        self.assertIn("EGRESS_ALLOWED_HOSTS", worker["environment"])
        self.assertIn("HTTPS_PROXY", worker["environment"])

    def test_stateful_dependencies_have_healthchecks_and_persistent_volumes(self):
        services = self.compose["services"]
        for name in ("postgres", "redis", "minio"):
            self.assertIn("healthcheck", services[name])
            self.assertTrue(services[name].get("volumes"))
        self.assertEqual(set(self.compose["volumes"]), {
            "postgres_data", "redis_data", "object_data",
        })


if __name__ == "__main__":
    unittest.main()
