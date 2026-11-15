from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.runs import create_runs_router
from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager


class RunsApiTests(unittest.TestCase):
    def setUp(self):
        self.stores = {}

        async def principal(request):
            return Principal(
                user_id="user-a", tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session", email="user@example.test", display_name="User",
            )

        async def lifecycle(p):
            store = self.stores.setdefault(p.tenant_id, InMemoryRunStateStore())
            return RunLifecycleManager(store)

        async def authorize(p, resource_type, resource_id, action):
            return resource_type == "run" and action in {"read", "cancel"}

        app = FastAPI()
        app.include_router(create_runs_router(
            resolve_principal=principal, resolve_lifecycle=lifecycle, authorize=authorize,
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


if __name__ == "__main__":
    unittest.main()
