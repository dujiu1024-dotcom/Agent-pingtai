from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.quotas import create_quota_reporting_router
from agent_platform.identity.models import Principal
from agent_platform.quotas.service import InMemoryQuotaStore, QuotaService, UsageEvent


class FakeQuotaStore:
    def __init__(self):
        self.calls = []
        self.reconciliation_calls = []


    async def reconcile_cost(self, tenant_id, start, end, *, source, actual_cost_usd):
        self.reconciliation_calls.append((tenant_id, start, end, source, actual_cost_usd))
        return {
            "reconciliation_id": "recon-1", "tenant_id": tenant_id,
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "source": source, "estimated_cost_usd": "1.25",
            "actual_cost_usd": str(actual_cost_usd),
            "variance_usd": str(actual_cost_usd - Decimal("1.25")),
        }

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




    def test_cost_reconciliation_is_tenant_bound_and_reports_variance(self):
        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=self.store, authorize_manage=lambda actor: actor.user_id == "user-a",
        ))
        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)
        client = TestClient(app)
        response = client.post("/api/platform/v1/quotas/reconciliation", json={
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "source": "invoice-2026-11",
            "actual_cost_usd": "1.50",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["tenant_id"], "tenant-a")
        self.assertEqual(response.json()["estimated_cost_usd"], "1.25")
        self.assertEqual(response.json()["variance_usd"], "0.25")
        self.assertEqual(self.store.reconciliation_calls[0][0], "tenant-a")

    def test_cost_reconciliation_rejects_unauthorized_and_invalid_amount(self):
        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=self.store, authorize_manage=lambda actor: False,
        ))
        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)
        client = TestClient(app)
        body = {
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "source": "invoice-2026-11", "actual_cost_usd": "1.50",
        }
        self.assertEqual(client.post("/api/platform/v1/quotas/reconciliation", json=body).status_code, 403)
        app2 = FastAPI()
        app2.include_router(create_quota_reporting_router(
            quota_store=self.store, authorize_manage=lambda actor: True,
        ))
        @app2.middleware("http")
        async def attach_principal2(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)
        body["actual_cost_usd"] = "NaN"
        self.assertEqual(TestClient(app2).post("/api/platform/v1/quotas/reconciliation", json=body).status_code, 400)

    def test_budget_configuration_requires_authorization_and_tenant_scope(self):
        service = QuotaService(InMemoryQuotaStore())
        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=self.store, quota_service=service, audit_writer=lambda event: None,
            authorize_manage=lambda actor: actor.user_id == "user-a",
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)

        client = TestClient(app)
        body = {
            "scope_type": "tenant", "scope_id": "tenant-a",
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "hard_tokens": 10000, "hard_cost_usd": "25.00",
            "hard_concurrency": 5, "soft_tokens": 8000,
        }
        response = client.post("/api/platform/v1/quotas/budgets", json=body)
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["configured"])
        body["scope_id"] = "tenant-b"
        denied = client.post("/api/platform/v1/quotas/budgets", json=body)
        self.assertEqual(denied.status_code, 403)


    def test_budget_configuration_supports_split_token_caps(self):
        service = QuotaService(InMemoryQuotaStore())
        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=self.store, quota_service=service, audit_writer=lambda event: None,
            authorize_manage=lambda actor: True,
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)

        client = TestClient(app)
        body = {
            "scope_type": "tenant", "scope_id": "tenant-a",
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "hard_tokens": 1000, "hard_input_tokens": 700,
            "hard_output_tokens": 300, "hard_concurrency": 4,
        }
        response = client.post("/api/platform/v1/quotas/budgets", json=body)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["hard_input_tokens"], 700)
        self.assertEqual(response.json()["hard_output_tokens"], 300)

    def test_budget_configuration_rejects_bool_string_and_infinite_limits(self):
        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=self.store, quota_service=QuotaService(InMemoryQuotaStore()), audit_writer=lambda event: None,
            authorize_manage=lambda actor: True,
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)

        client = TestClient(app)
        base = {
            "scope_type": "tenant", "scope_id": "tenant-a",
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
        }
        for field, value in (("hard_input_tokens", True), ("hard_output_tokens", "20"),
                             ("hard_concurrency", 1.5), ("hard_cost_usd", "Infinity")):
            response = client.post("/api/platform/v1/quotas/budgets", json={**base, field: value})
            self.assertEqual(response.status_code, 400, (field, response.text))


    def test_queue_and_worker_pool_budgets_require_resource_authorization(self):
        service = QuotaService(InMemoryQuotaStore())
        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=self.store, quota_service=service, audit_writer=lambda event: None,
            authorize_manage=lambda actor: True,
            authorize_scope=lambda actor, scope_type, scope_id: scope_id in {"priority", "gpu"},
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)

        client = TestClient(app)
        base = {
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "hard_concurrency": 3,
        }
        queue = client.post("/api/platform/v1/quotas/budgets", json={
            **base, "scope_type": "queue", "scope_id": "priority",
        })
        self.assertEqual(queue.status_code, 201, queue.text)
        worker_pool = client.post("/api/platform/v1/quotas/budgets", json={
            **base, "scope_type": "worker_pool", "scope_id": "gpu",
        })
        self.assertEqual(worker_pool.status_code, 201, worker_pool.text)
        denied = client.post("/api/platform/v1/quotas/budgets", json={
            **base, "scope_type": "queue", "scope_id": "other",
        })
        self.assertEqual(denied.status_code, 403)

    def test_resource_budget_requires_explicit_scope_authorizer(self):
        service = QuotaService(InMemoryQuotaStore())
        app = FastAPI()
        app.include_router(create_quota_reporting_router(
            quota_store=self.store, quota_service=service, audit_writer=lambda event: None,
            authorize_manage=lambda actor: True,
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="a@example.test", display_name="A",
                )
            })()
            return await call_next(request)

        response = TestClient(app).post("/api/platform/v1/quotas/budgets", json={
            "scope_type": "project", "scope_id": "project-other",
            "period_start": "2026-11-01T00:00:00+00:00",
            "period_end": "2026-12-01T00:00:00+00:00",
            "hard_tokens": 1000,
        })
        self.assertEqual(response.status_code, 403)

    def test_reconciliation_route_is_registered_once(self):
        router = create_quota_reporting_router(quota_store=self.store)
        routes = [
            route for route in router.routes
            if getattr(route, "path", None) == "/api/platform/v1/quotas/reconciliation"
            and "POST" in getattr(route, "methods", set())
        ]
        self.assertEqual(len(routes), 1)

if __name__ == "__main__":
    unittest.main()
