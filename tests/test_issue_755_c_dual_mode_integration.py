"""Dual-mode HTTP integration evidence for UI-G1-755-c.

These tests exercise the production FastAPI composition with HTTPS TestClient.
No live server, external identity provider, or real infrastructure is required.
"""
from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi.testclient import TestClient

from agent_platform.api.app import create_platform_app
from agent_platform.identity.models import AuthorizationContext, Principal
from agent_platform.identity.service import AuthenticationError
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import InMemoryMemoryRepository, MemoryService


class _Identity:
    async def authenticate(self, token: str):
        if token != "valid-session":
            raise AuthenticationError("invalid")
        return Principal(
            user_id="user-a", tenant_id="tenant-a", session_id="session-a",
            email="a@example.test", display_name="A",
        )

    async def authenticate_api_credential(self, token: str):
        raise AuthenticationError("invalid")


class _IdentityRepository:
    async def get_authorization_context(self, *, tenant_id: str, user_id: str):
        if (tenant_id, user_id) != ("tenant-a", "user-a"):
            return None
        return AuthorizationContext(
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_roles=frozenset({"tenant_admin"}),
            effective_role_ids=frozenset({"tenant_admin"}),
            tenant_permissions=frozenset({"knowledge.manage", "memory.manage"}),
            project_ids=frozenset(),
            knowledge_manage_project_ids=frozenset(),
            memory_manage_project_ids=frozenset(),
            agent_ids=frozenset(),
            memory_manage_agent_ids=frozenset(),
        )


class _Redis:
    async def eval(self, *args):
        return 1


class _OfficeState:
    async def build(self, tenant_id: str):
        return {"tenant_id": tenant_id, "tasks": [], "agents": []}


class DualModeIntegrationTests(unittest.TestCase):
    def _app(self, *, auth_enabled: bool):
        return create_platform_app(
            auth_enabled=auth_enabled,
            identity=_Identity() if auth_enabled else None,
            identity_repository=_IdentityRepository(),
            knowledge=KnowledgeService(InMemoryKnowledgeRepository()),
            memory=MemoryService(InMemoryMemoryRepository()),
            redis=_Redis(),
            allowed_origins=("https://console.example.test",),
            office_state_service=_OfficeState(),
            office_tenant_resolver=lambda _request: "instance-workspace",
            bootstrap_capabilities=("knowledge", "memory", "office"),
        )

    def test_no_auth_without_token_or_cookie_reaches_real_business_handlers(self):
        app = self._app(auth_enabled=False)
        client = TestClient(app, base_url="https://platform.example.test")

        # Deliberately send neither Authorization nor Cookie.
        requests = (
            ("/api/platform/v1/bootstrap", 200),
            ("/api/platform/v1/workbench/capabilities", 200),
            ("/api/platform/v1/office/state", 200),
            ("/api/platform/v1/knowledge/documents", 200),
        )
        for path, expected_status in requests:
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, expected_status, response.text)
                self.assertNotIn("Authorization", response.request.headers)
                self.assertNotIn("Cookie", response.request.headers)
                self.assertEqual(response.headers.get("x-content-type-options"), "nosniff")

        bootstrap = client.get("/api/platform/v1/bootstrap").json()
        self.assertIs(bootstrap["auth"]["enabled"], False)
        office = client.get("/api/platform/v1/office/state").json()
        self.assertEqual(office["data"]["tenant_id"], "instance-workspace")

    def test_auth_mode_requires_credentials_for_business_routes_but_keeps_bootstrap_public(self):
        client = TestClient(self._app(auth_enabled=True), base_url="https://platform.example.test")

        public = client.get("/api/platform/v1/bootstrap")
        self.assertEqual(public.status_code, 200)
        self.assertIs(public.json()["auth"]["enabled"], True)

        for path in (
            "/api/platform/v1/workbench/capabilities",
            "/api/platform/v1/knowledge/documents",
            "/api/platform/v1/office/state",
        ):
            with self.subTest(path=path):
                response = client.get(path)
                self.assertEqual(response.status_code, 401, response.text)

        authenticated = client.get(
            "/api/platform/v1/knowledge/documents",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(authenticated.status_code, 200, authenticated.text)

    def test_endpoint_matrix_distinguishes_mounted_from_optional_unmounted_routers(self):
        app = self._app(auth_enabled=False)
        # FastAPI's composed router may be deferred until OpenAPI generation.
        # Inspect the generated route contract rather than app.routes internals.
        mounted = set(app.openapi()["paths"])
        self.assertIn("/api/platform/v1/bootstrap", mounted)
        self.assertIn("/api/platform/v1/workbench/capabilities", mounted)
        self.assertIn("/api/platform/v1/office/state", mounted)
        self.assertIn("/api/platform/v1/knowledge/documents", mounted)

        # Optional services are not mounted in this fixture. A 404 for these
        # paths is not evidence that no-auth succeeded.
        for path in (
            "/api/platform/v1/workbench/tasks",
            "/api/platform/v1/runs/{run_id}",
            "/api/platform/v1/approvals",
            "/api/platform/v1/sessions",
            "/api/platform/v1/files",
            "/api/platform/v1/registry/agent",
        ):
            with self.subTest(path=path):
                self.assertNotIn(path, mounted)


if __name__ == "__main__":
    unittest.main()
