from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.runs import create_runs_router
from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager


class ToolEffectReconciliationApiTests(unittest.TestCase):
    def setUp(self):
        self.run_store = InMemoryRunStateStore()
        asyncio.run(self.run_store.create("tenant-a", "run-1"))
        self.authorized = True
        self.reconciliations = []
        now = datetime.now(timezone.utc)
        self.effect = SimpleNamespace(
            tenant_id="tenant-a", run_id="run-1", call_id="call-1",
            action_digest="a" * 64, actor_id="user-a", state="pending",
            result=None, error="provider timeout", created_at=now, updated_at=now,
            resolution=None, reconciled_by=None, reconciliation_note=None,
            reconciled_at=None,
        )

        async def resolve_principal(request):
            return Principal(
                user_id="operator-a",
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session-a", email="operator@example.test",
                display_name="Operator",
            )

        async def resolve_lifecycle(_principal):
            return RunLifecycleManager(self.run_store)

        async def authorize(_principal, resource_type, _resource_id, action):
            return resource_type == "run" and (
                action == "read" or (action == "reconcile" and self.authorized)
            )

        class Store:
            async def get(inner, *, tenant_id, run_id, call_id):
                if (tenant_id, run_id, call_id) == ("tenant-a", "run-1", "call-1"):
                    return self.effect
                return None

        class ReconciliationService:
            store = Store()

            async def reconcile(inner, **kwargs):
                self.reconciliations.append(kwargs)
                self.effect.state = "succeeded"
                self.effect.resolution = kwargs["resolution"]
                self.effect.reconciled_by = kwargs["operator_id"]
                self.effect.reconciliation_note = kwargs["note"]
                self.effect.reconciled_at = datetime.now(timezone.utc)
                self.effect.result = kwargs["result"]
                return self.effect

        async def resolve_tool_effects(_principal):
            return ReconciliationService()

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=resolve_principal,
            resolve_lifecycle=resolve_lifecycle,
            authorize=authorize,
            resolve_tool_effects=resolve_tool_effects,
        ))
        self.client = TestClient(app)

    def test_authorized_operator_can_reconcile_and_audit_identity_is_forwarded(self):
        response = self.client.post(
            "/api/platform/v1/runs/run-1/tool-effects/call-1/reconcile",
            json={
                "resolution": "confirmed_succeeded",
                "note": "Verified in provider dashboard",
                "result": {"provider_operation_id": "op-123"},
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["state"], "succeeded")
        self.assertEqual(response.json()["reconciled_by"], "operator-a")
        self.assertEqual(self.reconciliations[0]["operator_id"], "operator-a")
        self.assertEqual(self.reconciliations[0]["tenant_id"], "tenant-a")

    def test_reconciliation_requires_run_reconcile_permission(self):
        self.authorized = False
        response = self.client.post(
            "/api/platform/v1/runs/run-1/tool-effects/call-1/reconcile",
            json={"resolution": "confirmed_not_applied", "note": "Checked provider"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.reconciliations, [])

    def test_reconciliation_is_tenant_scoped(self):
        response = self.client.post(
            "/api/platform/v1/runs/run-1/tool-effects/call-1/reconcile",
            headers={"x-tenant": "tenant-b"},
            json={"resolution": "confirmed_not_applied", "note": "Checked provider"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.reconciliations, [])

    def test_reconciliation_requires_resolution_and_note(self):
        response = self.client.post(
            "/api/platform/v1/runs/run-1/tool-effects/call-1/reconcile",
            json={"resolution": "confirmed_not_applied"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.reconciliations, [])


if __name__ == "__main__":
    unittest.main()
