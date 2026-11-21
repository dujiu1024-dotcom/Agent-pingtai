from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.observability import create_observability_router
from agent_platform.identity.models import Principal


class OperationalHealthApiTests(unittest.TestCase):
    def _client(self, monitor):
        app = FastAPI()
        app.include_router(create_observability_router(
            metrics=None, operational_health_monitor=monitor,
        ))
        @app.middleware("http")
        async def identity(request, call_next):
            request.state.__dict__.setdefault("_state", {})["identity.principal"] = Principal(
                user_id="operator-a", tenant_id="tenant-a", session_id="session-a",
                email="operator@example.invalid", display_name="Operator",
            )
            return await call_next(request)
        return TestClient(app)

    def test_capacity_health_endpoint_returns_probe_report(self):
        class Monitor:
            async def evaluate(self):
                return {"status": "warning", "capacity": {}, "certificates": {}}

        response = self._client(Monitor()).get(
            "/api/platform/v1/observability/health/capacity",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "warning")

    def test_critical_health_endpoint_returns_503(self):
        class Monitor:
            async def evaluate(self):
                return {"status": "critical", "capacity": {}, "certificates": {}}

        response = self._client(Monitor()).get(
            "/api/platform/v1/observability/health/capacity",
        )
        self.assertEqual(response.status_code, 503)

    def test_missing_monitor_returns_503(self):
        client = self._client(None)
        response = client.get("/api/platform/v1/observability/health/capacity")
        self.assertEqual(response.status_code, 503)

    def test_unauthenticated_request_is_rejected(self):
        app = FastAPI()
        app.include_router(create_observability_router(
            metrics=None, operational_health_monitor=object(),
        ))
        response = TestClient(app).get(
            "/api/platform/v1/observability/health/capacity",
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
