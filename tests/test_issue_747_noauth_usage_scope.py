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


    def test_no_auth_mode_reads_tenant_usage_without_user_or_department_filtering(self):
        store = Store()
        # Two rows from different users/departments are returned by the tenant
        # ledger. The reporting API intentionally scopes by tenant only and
        # never derives a user/department predicate from the anon principal.
        async def list_usage(tenant_id, start, end, *, run_id=None, model_key=None, limit=1000):
            store.tenant_id = tenant_id
            store.query_dimensions = {"run_id": run_id, "model_key": model_key}
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
        store.list_usage = list_usage
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
            request.state.__dict__["identity.authenticated"] = False
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

    def test_budget_mutation_fails_closed_without_audit_writer(self):
        class Service:
            def __init__(self):
                self.configured = []
            async def configure(self, budget):
                self.configured.append(budget)

        service = Service()
        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=Store(), quota_service=service,
            authorize_manage=lambda _principal: True,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="admin", tenant_id="tenant-1", session_id="s",
                    email="admin@example.test", display_name="Admin",
                )
            })()
            return await call_next(request)

        response = TestClient(app).post("/api/platform/v1/quotas/budgets", json={
            "scope_type": "tenant", "scope_id": "tenant-1",
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "hard_tokens": 100,
        })
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(service.configured, [])

    def test_budget_mutation_fails_closed_when_audit_write_fails(self):
        class Service:
            def __init__(self):
                self.configured = []
            async def configure(self, budget):
                self.configured.append(budget)

        service = Service()
        def broken_audit(_event):
            raise RuntimeError("audit unavailable")

        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=Store(), quota_service=service,
            authorize_manage=lambda _principal: True, audit_writer=broken_audit,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="admin", tenant_id="tenant-1", session_id="s",
                    email="admin@example.test", display_name="Admin",
                )
            })()
            return await call_next(request)

        response = TestClient(app).post("/api/platform/v1/quotas/budgets", json={
            "scope_type": "tenant", "scope_id": "tenant-1",
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "hard_tokens": 100,
        })
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(service.configured, [])


if __name__ == "__main__":
    unittest.main()
