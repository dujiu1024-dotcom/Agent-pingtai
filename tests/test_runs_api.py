from __future__ import annotations

import unittest
from decimal import Decimal
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.runs import create_runs_router
from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager
from agent_platform.tools.idempotency import (
    InMemoryToolEffectStore,
    ToolEffectReconciliationService,
)


class RunsApiTests(unittest.TestCase):
    def setUp(self):
        self.stores = {}
        self.effects = InMemoryToolEffectStore()
        self.allow_reconcile = True
        self.audit_records = []
        self.budget_calls = []

        class BudgetReader:
            async def snapshot(inner, tenant_id, run_id):
                if tenant_id != "tenant-a":
                    raise KeyError(run_id)
                return SimpleNamespace(
                    max_tokens=1000, max_cost_usd=Decimal("5.00"),
                    reserved_tokens=20, reserved_cost_usd=Decimal("0.10"),
                    actual_tokens=50, actual_cost_usd=Decimal("0.25"),
                    remaining_tokens=930, remaining_cost_usd=Decimal("4.65"),
                    overrun_reason=None,
                )

            async def list_model_calls(inner, *, tenant_id, run_id, limit=200):
                self.budget_calls.append((tenant_id, run_id, limit))
                return [{
                    "call_id": "call-budget-1", "tenant_id": tenant_id,
                    "run_id": run_id, "model_key": "model-a",
                    "estimated_input_tokens": 40, "estimated_output_tokens": 20,
                    "estimated_total_tokens": 60, "estimated_cost_usd": "0.30",
                    "actual_input_tokens": 30, "actual_output_tokens": 20,
                    "actual_total_tokens": 50, "actual_cost_usd": "0.25",
                    "token_delta": -10, "cost_delta_usd": "-0.05",
                    "remaining_tokens": 930, "remaining_cost_usd": "4.65",
                    "status": "settled", "overrun_reason": None,
                }]

        self.budget_reader = BudgetReader()

        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="user@example.test", display_name="User",
            )

        async def lifecycle(p):
            store = self.stores.setdefault(p.tenant_id, InMemoryRunStateStore())
            return RunLifecycleManager(store)

        async def authorize(p, resource_type, resource_id, action):
            return resource_type == "run" and (
                action in {"read", "cancel"} or (action == "reconcile" and self.allow_reconcile)
            )

        async def resolve_budget_ledger(p):
            return self.budget_reader

        async def resolve_tool_effects(p):
            async def authorize_operator(operator_id, tenant_id, run_id, call_id):
                return (
                    self.allow_reconcile and operator_id == p.user_id
                    and tenant_id == p.tenant_id
                )

            class Audit:
                async def record(inner_self, **record):
                    self.audit_records.append(record)

            return ToolEffectReconciliationService(
                store=self.effects, authorize_operator=authorize_operator,
                audit_sink=Audit(),
            )

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=principal, resolve_lifecycle=lifecycle, authorize=authorize,
            resolve_tool_effects=resolve_tool_effects,
            resolve_budget_ledger=resolve_budget_ledger,
        ))
        self.client = TestClient(app)

    def test_get_and_cancel_run(self):
        store = self.stores.setdefault("tenant-a", InMemoryRunStateStore())
        import asyncio
        asyncio.run(store.create("tenant-a", "run-1"))
        fetched = self.client.get("/api/platform/v1/runs/run-1")
        self.assertEqual(fetched.status_code, 200)
        cancelled = self.client.post("/api/platform/v1/runs/run-1/cancel")
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")

    def test_run_details_include_budget_snapshot_and_estimated_actual_call_usage(self):
        import asyncio
        store = self.stores.setdefault("tenant-a", InMemoryRunStateStore())
        asyncio.run(store.create("tenant-a", "run-budget"))
        response = self.client.get("/api/platform/v1/runs/run-budget")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["budget"]["max_tokens"], 1000)
        self.assertEqual(body["budget"]["actual_tokens"], 50)
        self.assertEqual(body["budget"]["remaining_tokens"], 930)
        self.assertEqual(body["model_call_usage"][0]["estimated_total_tokens"], 60)
        self.assertEqual(body["model_call_usage"][0]["actual_total_tokens"], 50)
        self.assertEqual(self.budget_calls, [("tenant-a", "run-budget", 200)])

    def test_cross_tenant_run_is_not_visible(self):
        store = self.stores.setdefault("tenant-a", InMemoryRunStateStore())
        import asyncio
        asyncio.run(store.create("tenant-a", "run-2"))
        response = self.client.get(
            "/api/platform/v1/runs/run-2", headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(response.status_code, 404)


    def test_tool_effect_reconciliation_requires_run_permission_and_records_resolution(self):
        import asyncio

        store = self.stores.setdefault("tenant-a", InMemoryRunStateStore())
        asyncio.run(store.create("tenant-a", "run-effects"))
        asyncio.run(self.effects.claim(
            tenant_id="tenant-a", run_id="run-effects", call_id="call-1",
            action_digest="a" * 64, actor_id="agent-a",
        ))
        asyncio.run(self.effects.fail(
            tenant_id="tenant-a", run_id="run-effects", call_id="call-1",
            error="provider_timeout",
        ))
        response = self.client.post(
            "/api/platform/v1/runs/run-effects/tool-effects/call-1/reconcile",
            json={
                "resolution": "confirmed_not_applied",
                "note": "External provider confirms no operation was created.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "retryable")
        self.assertEqual(response.json()["reconciled_by"], "user-a")
        self.assertEqual(
            [row["outcome"] for row in self.audit_records],
            ["reconciliation_started", "reconciliation_completed"],
        )
        fetched = self.client.get(
            "/api/platform/v1/runs/run-effects/tool-effects/call-1",
        )
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["resolution"], "confirmed_not_applied")

    def test_tool_effect_reconciliation_is_forbidden_without_operator_permission(self):
        import asyncio

        store = self.stores.setdefault("tenant-a", InMemoryRunStateStore())
        asyncio.run(store.create("tenant-a", "run-effects-denied"))
        asyncio.run(self.effects.claim(
            tenant_id="tenant-a", run_id="run-effects-denied", call_id="call-2",
            action_digest="b" * 64, actor_id="agent-a",
        ))
        asyncio.run(self.effects.fail(
            tenant_id="tenant-a", run_id="run-effects-denied", call_id="call-2",
            error="timeout",
        ))
        self.allow_reconcile = False
        response = self.client.post(
            "/api/platform/v1/runs/run-effects-denied/tool-effects/call-2/reconcile",
            json={"resolution": "confirmed_not_applied", "note": "checked"},
        )
        self.assertEqual(response.status_code, 403)
        effect = asyncio.run(self.effects.get(
            tenant_id="tenant-a", run_id="run-effects-denied", call_id="call-2",
        ))
        self.assertEqual(effect.state, "failed")



if __name__ == "__main__":
    unittest.main()
