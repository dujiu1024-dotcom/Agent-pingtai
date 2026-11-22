from __future__ import annotations

import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.observability import create_observability_router
from agent_platform.identity.models import Principal


class EmptyMetrics:
    def export_json(self):
        return []


def _client() -> TestClient:
    app = FastAPI()
    app.include_router(create_observability_router(metrics=EmptyMetrics()))

    @app.middleware("http")
    async def identity(request, call_next):
        request.state.__setattr__(
            "identity.principal",
            Principal("user-1", "tenant-a", "session-1", "user@example.com", "User"),
        )
        return await call_next(request)

    return TestClient(app)


class ObservabilityMetricsApiTests(unittest.TestCase):
    def test_metrics_endpoint_returns_database_fallback_records(self):
        client = _client()
        records = [{"name": "运行总数", "value": 4, "labels": {"状态": "失败"}}]
        with patch(
            "agent_platform.api.observability._fallback_db_metrics",
            return_value=records,
        ):
            response = client.get("/api/platform/v1/observability/metrics/json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": records, "count": 1})

    def test_metrics_endpoint_surfaces_database_failure_instead_of_empty_success(self):
        client = _client()

        async def unavailable():
            raise OSError("database unavailable")

        with patch(
            "agent_platform.api.observability._fallback_db_metrics",
            side_effect=unavailable,
        ):
            response = client.get("/api/platform/v1/observability/metrics/json")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"],
            "metrics fallback query is unavailable",
        )


if __name__ == "__main__":
    unittest.main()
