from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.audit import create_audit_router
from agent_platform.api.runs import create_runs_router
from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager


class BudgetReader:
    def __init__(self):
        self.calls = []

    async def get_run_budget_report(self, tenant_id, run_id):
        self.calls.append((tenant_id, run_id))
        return {
            "tenant_id": tenant_id,
            "run_id": run_id,
            "budget": {
                "max_tokens": 1000,
                "max_cost_usd": "1.00000000",
                "reserved_tokens": 20,
                "reserved_cost_usd": "0.02000000",
                "actual_tokens": 120,
                "actual_cost_usd": "0.12000000",
                "remaining_tokens": 860,
                "remaining_cost_usd": "0.86000000",
                "overrun_reason": None,
            },
            "calls": [{
                "call_id": "call-1",
                "model_key": "model-a",
                "status": "settled",
                "estimated_input_tokens": 80,
                "estimated_output_tokens": 40,
                "estimated_total_tokens": 120,
                "estimated_cost_usd": "0.10000000",
                "actual_input_tokens": 90,
                "actual_output_tokens": 30,
                "actual_total_tokens": 120,
                "actual_cost_usd": "0.12000000",
                "token_delta": 0,
                "cost_delta_usd": "0.02000000",
                "remaining_tokens": 860,
                "remaining_cost_usd": "0.86000000",
                "overrun_reason": None,
                "created_at": datetime(2026, 11, 18, tzinfo=timezone.utc).isoformat(),
                "settled_at": datetime(2026, 11, 18, tzinfo=timezone.utc).isoformat(),
            }],
            "summary": {
                "call_count": 1, "settled_calls": 1, "reserved_calls": 0,
                "rejected_calls": 0, "estimated_tokens": 120, "actual_tokens": 120,
                "estimated_cost_usd": "0.10000000", "actual_cost_usd": "0.12000000",
            },
        }


class RunBudgetUsageReadbackTests(unittest.TestCase):
    def setUp(self):
        self.reader = BudgetReader()
        self.store = InMemoryRunStateStore()

        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="user@example.test", display_name="User",
            )

        async def lifecycle(p):
            return RunLifecycleManager(self.store)

        async def authorize(p, resource_type, resource_id, action):
            return resource_type == "run" and action == "read"

        async def resolve_reader(_principal):
            return self.reader

        import asyncio
        asyncio.run(self.store.create("tenant-a", "run-1"))
        self.runs_app = FastAPI()
        self.runs_app.include_router(create_runs_router(
            resolve_principal=principal, resolve_lifecycle=lifecycle,
            authorize=authorize, resolve_run_budget_ledger=resolve_reader,
        ))
        self.runs_client = TestClient(self.runs_app)

        self.audit_app = FastAPI()
        self.audit_app.include_router(create_audit_router(
            reader=object(), authorize_read=lambda _principal: True,
            run_budget_reader=self.reader,
        ))

        @self.audit_app.middleware("http")
        async def identity(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="auditor", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                    session_id="session", email="audit@example.test", display_name="Auditor",
                )
            })()
            return await call_next(request)

        self.audit_client = TestClient(self.audit_app)

    def test_run_details_expose_budget_estimates_and_provider_actuals(self):
        response = self.runs_client.get("/api/platform/v1/runs/run-1/budget-usage")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["budget"]["max_tokens"], 1000)
        self.assertEqual(body["budget"]["actual_tokens"], 120)
        self.assertEqual(body["calls"][0]["estimated_total_tokens"], 120)
        self.assertEqual(body["calls"][0]["actual_cost_usd"], "0.12000000")
        self.assertEqual(body["calls"][0]["cost_delta_usd"], "0.02000000")
        self.assertEqual(self.reader.calls[-1], ("tenant-a", "run-1"))

    def test_audit_readback_is_tenant_bound(self):
        response = self.audit_client.get(
            "/api/platform/v1/audit/run-budget-usage", params={"run_id": "run-1"},
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["tenant_id"], "tenant-b")
        self.assertEqual(self.reader.calls[-1], ("tenant-b", "run-1"))

    def test_run_detail_requires_run_read_authorization(self):
        async def denied(_p, _kind, _id, _action):
            return False

        app = FastAPI()

        async def principal(_request):
            return Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="s",
                email="user@example.test", display_name="User",
            )

        async def lifecycle(_principal):
            return RunLifecycleManager(self.store)

        async def resolve_reader(_principal):
            return self.reader

        app.include_router(create_runs_router(
            resolve_principal=principal, resolve_lifecycle=lifecycle,
            authorize=denied, resolve_run_budget_ledger=resolve_reader,
        ))
        response = TestClient(app).get("/api/platform/v1/runs/run-1/budget-usage")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reader.calls, [])


if __name__ == "__main__":
    unittest.main()
