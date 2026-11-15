from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.quotas import create_quota_reporting_router
from agent_platform.identity.models import Principal
from agent_platform.quotas.service import UsageEvent


class FakeQuotaStore:
    def __init__(self):
        self.calls = []

    async def list_usage(self, tenant_id, start, end, *, run_id=None, model_key=None, limit=1000):
        self.calls.append((tenant_id, start, end, run_id, model_key, limit))
        if tenant_id != "tenant-a":
            return []
        return [UsageEvent(
            event_id="event-1", tenant_id=tenant_id, run_id="run-1",
            model_key="general@1", input_tokens=10, output_tokens=5,
            cost_usd=Decimal("0.002"), occurred_at=datetime(2026, 11, 16, tzinfo=timezone.utc),
            project_id="project-1", agent_id="agent-1", tool_calls=2,
            runtime_ms=1200, storage_bytes=256, price_card_version="2026-11",
            source="provider_reported",
        )]


class QuotaReportingApiTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeQuotaStore()
        app = FastAPI()
        app.include_router(create_quota_reporting_router(quota_store=self.store))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)

        self.client = TestClient(app)
        self.params = {
            "start": "2026-11-15T00:00:00+00:00",
            "end": "2026-11-17T00:00:00+00:00",
        }

    def test_usage_report_is_tenant_bound_and_contains_cost_dimensions(self):
        response = self.client.get("/api/platform/v1/quotas/usage", params=self.params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tenant_id"], "tenant-a")
        item = response.json()["items"][0]
        self.assertEqual(item["total_tokens"], 15)
        self.assertEqual(item["cost_usd"], "0.002")
        self.assertEqual(item["project_id"], "project-1")
        self.assertEqual(self.store.calls[0][0], "tenant-a")

    def test_csv_export_and_range_limit(self):
        response = self.client.get("/api/platform/v1/quotas/usage.csv", params=self.params)
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("provider_reported", response.text)
        too_wide = self.client.get("/api/platform/v1/quotas/usage", params={
            "start": "2025-01-01T00:00:00+00:00",
            "end": "2026-11-17T00:00:00+00:00",
        })
        self.assertEqual(too_wide.status_code, 400)


if __name__ == "__main__":
    unittest.main()
