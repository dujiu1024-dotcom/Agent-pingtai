from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.audit import create_audit_router
from agent_platform.identity.models import Principal


class FakeAuditReader:
    async def list_events(self, *, tenant_id, limit, actor_id=None, run_id=None):
        return ({"tenant_id": tenant_id, "actor_id": actor_id or "actor-a",
                 "run_id": run_id, "action": "tool.invoke"},)


class AuditApiTests(unittest.TestCase):
    def setUp(self):
        self.principal = Principal(
            tenant_id="tenant-a", user_id="user-a", session_id="session-a",
            email="user@example.test", display_name="Audit User",
        )

    def client(self, allowed):
        app = FastAPI()
        @app.middleware("http")
        async def identity(request, call_next):
            request.state.identity = SimpleNamespace(principal=self.principal)
            return await call_next(request)
        app.include_router(create_audit_router(
            reader=FakeAuditReader(), authorize_read=lambda principal: allowed,
        ))
        return TestClient(app)

    def test_authorized_audit_query_is_tenant_scoped(self):
        response = self.client(True).get("/api/platform/v1/audit/tool-events?run_id=run-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tenant_id"], "tenant-a")
        self.assertEqual(response.json()["items"][0]["run_id"], "run-1")

    def test_audit_query_requires_explicit_permission(self):
        response = self.client(False).get("/api/platform/v1/audit/tool-events")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
