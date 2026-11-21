from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.quotas import create_quota_reporting_router
from agent_platform.identity.models import Principal
from agent_platform.quotas.service import UsageEvent


class TenantUsageStore:
    def __init__(self):
        self.tenant_id = None
        self.query_dimensions = None

    async def list_usage(self, tenant_id, start, end, *, run_id=None, model_key=None, limit=1000):
        self.tenant_id = tenant_id
        self.query_dimensions = {"run_id": run_id, "model_key": model_key}
        return [
            UsageEvent(
                event_id="event-user-a", tenant_id=tenant_id, run_id="run-a",
                model_key="model-a", input_tokens=10, output_tokens=5,
                cost_usd=Decimal("0.004"),
                occurred_at=datetime(2026, 11, 17, tzinfo=timezone.utc),
                project_id="project-a", agent_id="agent-a", tool_calls=1,
                runtime_ms=100, storage_bytes=0, price_card_version="v7",
                source="estimated",
            ),
            UsageEvent(
                event_id="event-user-b", tenant_id=tenant_id, run_id="run-b",
                model_key="model-b", input_tokens=20, output_tokens=10,
                cost_usd=Decimal("0.008"),
                occurred_at=datetime(2026, 11, 17, tzinfo=timezone.utc),
                project_id="project-b", agent_id="agent-b", tool_calls=2,
                runtime_ms=200, storage_bytes=0, price_card_version="v7",
                source="estimated",
            ),
        ]


class NoAuthUsageScopeTests(unittest.TestCase):
    def test_no_auth_reads_tenant_usage_without_user_or_department_filtering(self):
        store = TenantUsageStore()
        app = FastAPI()
        app.include_router(create_quota_reporting_router(quota_store=store))

        @app.middleware("http")
        async def no_auth_identity(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="anon", tenant_id="default", session_id="no-auth",
                    email="", display_name="Anonymous",
                )
            })()
            setattr(request.state, "identity.authenticated", False)
            return await call_next(request)

        response = TestClient(app).get(
            "/api/platform/v1/quotas/usage",
            params={
                "start": "2026-11-16T00:00:00+00:00",
                "end": "2026-11-18T00:00:00+00:00",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(store.tenant_id, "default")
        self.assertEqual(store.query_dimensions, {"run_id": None, "model_key": None})
        self.assertEqual(
            [row["event_id"] for row in response.json()["items"]],
            ["event-user-a", "event-user-b"],
        )

    def test_project_and_agent_are_explicit_analytics_filters(self):
        store = TenantUsageStore()
        app = FastAPI()
        app.include_router(create_quota_reporting_router(quota_store=store))

        @app.middleware("http")
        async def no_auth_identity(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="anon", tenant_id="default", session_id="no-auth",
                    email="", display_name="Anonymous",
                )
            })()
            setattr(request.state, "identity.authenticated", False)
            return await call_next(request)

        response = TestClient(app).get(
            "/api/platform/v1/quotas/usage/summary",
            params={
                "start": "2026-11-16T00:00:00+00:00",
                "end": "2026-11-18T00:00:00+00:00",
                "project_id": "project-b",
                "agent_id": "agent-b",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["events"], 1)
        self.assertEqual(body["by_project"]["project-b"]["events"], 1)
        self.assertEqual(body["by_agent"]["agent-b"]["events"], 1)


if __name__ == "__main__":
    unittest.main()
