from __future__ import annotations

import asyncio
import unittest
from decimal import Decimal
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.audit import create_audit_router
from agent_platform.api.runs import create_runs_router
from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager


class FakeBudgetLedger:
    def __init__(self):
        self.snapshot_calls = []
        self.usage_calls = []

    async def snapshot(self, tenant_id, run_id):
        self.snapshot_calls.append((tenant_id, run_id))
        if tenant_id != "tenant-a" or run_id != "run-a":
            raise KeyError("budget not found")
        return SimpleNamespace(
            max_tokens=1000,
            max_cost_usd=Decimal("2.50"),
            reserved_tokens=100,
            reserved_cost_usd=Decimal("0.25"),
            actual_tokens=90,
            actual_cost_usd=Decimal("0.21"),
            remaining_tokens=810,
            remaining_cost_usd=Decimal("2.04"),
            overrun_reason=None,
        )

    async def list_model_calls(self, *, tenant_id, run_id, limit=200):
        self.usage_calls.append((tenant_id, run_id, limit))
        if tenant_id != "tenant-a":
            return []
        return [{
            "call_id": "call-a",
            "estimated_input_tokens": 60,
            "estimated_output_tokens": 30,
            "estimated_cost_usd": "0.25",
            "actual_input_tokens": 55,
            "actual_output_tokens": 35,
            "actual_cost_usd": "0.21",
            "token_delta": 0,
            "cost_delta_usd": "-0.04",
            "status": "settled",
        }]


class RunBudgetVisibilityAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.store = InMemoryRunStateStore()
        asyncio.run(self.store.create("tenant-a", "run-a"))
        self.lifecycle = RunLifecycleManager(self.store)
        self.ledger = FakeBudgetLedger()

        async def principal(request):
            tenant_id = request.headers.get("x-tenant", "tenant-a")
            return Principal(
                user_id="operator-a",
                tenant_id=tenant_id,
                session_id="session-a",
                email="operator@example.test",
                display_name="Operator",
            )

        async def lifecycle_resolver(_principal):
            return self.lifecycle

        async def authorize(actor, resource_type, resource_id, action):
            return (
                actor.tenant_id == "tenant-a"
                and resource_type == "run"
                and resource_id == "run-a"
                and action == "read"
            )

        async def ledger_resolver(_principal):
            return self.ledger

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=principal,
            resolve_lifecycle=lifecycle_resolver,
            authorize=authorize,
            resolve_budget_ledger=ledger_resolver,
        ))
        self.run_client = TestClient(app)

        audit_app = FastAPI()

        @audit_app.middleware("http")
        async def attach_identity(request, call_next):
            request.state.identity = SimpleNamespace(principal=Principal(
                user_id="operator-a",
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session-a",
                email="operator@example.test",
                display_name="Operator",
            ))
            return await call_next(request)

        audit_app.include_router(create_audit_router(
            reader=None,
            authorize_read=lambda actor: actor.tenant_id == "tenant-a",
            budget_reader=self.ledger,
        ))
        self.audit_client = TestClient(audit_app)

    def test_run_detail_exposes_budget_snapshot_and_estimated_actual_usage(self):
        response = self.run_client.get("/api/platform/v1/runs/run-a")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["budget"]["max_tokens"], 1000)
        self.assertEqual(body["budget"]["actual_tokens"], 90)
        self.assertEqual(body["budget"]["actual_cost_usd"], "0.21")
        self.assertEqual(body["budget"]["remaining_tokens"], 810)
        self.assertEqual(body["model_call_usage"][0]["estimated_input_tokens"], 60)
        self.assertEqual(body["model_call_usage"][0]["actual_input_tokens"], 55)
        self.assertEqual(body["model_call_usage"][0]["cost_delta_usd"], "-0.04")
        self.assertEqual(self.ledger.snapshot_calls, [("tenant-a", "run-a")])
        self.assertEqual(self.ledger.usage_calls, [("tenant-a", "run-a", 200)])

    def test_run_detail_denies_cross_tenant_access_before_reading_budget(self):
        response = self.run_client.get(
            "/api/platform/v1/runs/run-a", headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.ledger.snapshot_calls, [])
        self.assertEqual(self.ledger.usage_calls, [])

    def test_budget_audit_endpoint_returns_estimate_actual_and_is_tenant_scoped(self):
        response = self.audit_client.get(
            "/api/platform/v1/audit/run-budget-events",
            params={"run_id": "run-a", "limit": 20},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["tenant_id"], "tenant-a")
        self.assertEqual(body["run_id"], "run-a")
        self.assertEqual(body["items"][0]["estimated_cost_usd"], "0.25")
        self.assertEqual(body["items"][0]["actual_cost_usd"], "0.21")
        self.assertEqual(body["items"][0]["cost_delta_usd"], "-0.04")
        self.assertEqual(self.ledger.usage_calls[-1], ("tenant-a", "run-a", 20))

    def test_budget_audit_endpoint_requires_audit_permission(self):
        response = self.audit_client.get(
            "/api/platform/v1/audit/run-budget-events",
            params={"run_id": "run-a"},
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
