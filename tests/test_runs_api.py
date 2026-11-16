from __future__ import annotations

import unittest

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
