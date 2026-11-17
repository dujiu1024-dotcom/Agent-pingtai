from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.quotas import create_quota_reporting_router
from agent_platform.identity.models import Principal
from agent_platform.quotas.service import UsageEvent


class Store:
    async def list_usage(self, tenant_id, start, end, *, run_id=None, model_key=None, limit=1000):
        self.tenant_id = tenant_id
        return [UsageEvent(
            event_id="event-1", tenant_id=tenant_id, run_id="run-1",
            model_key="model-a", input_tokens=10, output_tokens=5,
            cost_usd=Decimal("0.004"),
            occurred_at=datetime(2026, 11, 17, tzinfo=timezone.utc),
            project_id="project-a", agent_id="agent-a", tool_calls=2,
            runtime_ms=500, storage_bytes=128, price_card_version="v7",
            source="estimated",
        )]


class UsageSummaryApiTests(unittest.TestCase):
    def test_summary_is_tenant_bound_and_returns_resource_and_cost_dimensions(self):
        store = Store()
        app = FastAPI()
        app.include_router(create_quota_reporting_router(quota_store=store))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="u1", tenant_id="tenant-1", session_id="s1",
                    email="u@example.test", display_name="User",
                )
            })()
            return await call_next(request)

        response = TestClient(app).get(
            "/api/platform/v1/quotas/usage/summary",
            params={
                "start": "2026-11-16T00:00:00+00:00",
                "end": "2026-11-18T00:00:00+00:00",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.tenant_id, "tenant-1")
        body = response.json()
        self.assertEqual(body["tool_calls"], 2)
        self.assertEqual(body["storage_bytes"], 128)
        self.assertEqual(body["by_model"]["model-a"]["cost_usd"], "0.004")
        self.assertEqual(body["estimated_cost_usd"], "0.004")
        self.assertEqual(body["provider_reported_cost_usd"], "0")


if __name__ == "__main__":
    unittest.main()
