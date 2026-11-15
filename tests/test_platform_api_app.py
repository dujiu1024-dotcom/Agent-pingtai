"""Tests for production HTTP API composition."""
import unittest

from fastapi.testclient import TestClient

from agent_platform.api.app import create_platform_app
from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage, InMemorySessionRepository, SessionAssetService,
)
from agent_platform.identity.models import AuthorizationContext, Principal
from agent_platform.identity.service import AuthenticationError
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import InMemoryMemoryRepository, MemoryService


class FakeIdentity:
    async def authenticate(self, token):
        if token == "valid-session":
            return Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="session-a",
                email="a@example.com", display_name="A",
            )
        if token == "valid-session-b":
            return Principal(
                user_id="user-b", tenant_id="tenant-b", session_id="session-b",
                email="b@example.com", display_name="B",
            )
        raise AuthenticationError("invalid")

    async def authenticate_api_credential(self, token):
        raise AuthenticationError("invalid")


class FakeIdentityRepository:
    async def get_authorization_context(self, *, tenant_id, user_id):
        if tenant_id != "tenant-a" or user_id != "user-a":
            return None
        return AuthorizationContext(
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_roles=frozenset({"tenant_admin"}),
            tenant_permissions=frozenset({"knowledge.manage", "memory.manage"}),
            project_ids=frozenset(),
            knowledge_manage_project_ids=frozenset(),
            memory_manage_project_ids=frozenset(),
            agent_ids=frozenset(),
            memory_manage_agent_ids=frozenset(),
        )


class FakeRedis:
    async def eval(self, *args):
        return 1


class PlatformApiCompositionTests(unittest.TestCase):
    def setUp(self):
        self.identity = FakeIdentity()
        self.knowledge = KnowledgeService(InMemoryKnowledgeRepository())
        self.memory = MemoryService(InMemoryMemoryRepository())
        self.app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
        )
        self.client = TestClient(self.app, base_url="https://testserver")

    def test_business_routes_require_authenticated_session(self):
        response = self.client.get("/api/platform/v1/knowledge/documents")
        self.assertEqual(response.status_code, 401)

    def test_authenticated_session_is_resolved_into_business_principal(self):
        response = self.client.get(
            "/api/platform/v1/knowledge/documents",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], [])

    def test_auth_me_and_security_headers_are_available(self):
        response = self.client.get(
            "/auth/me",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["user_id"], "user-a")
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")

    def test_file_assets_are_isolated_by_tenant_in_authenticated_api(self):
        service = SessionAssetService(
            InMemorySessionRepository(), InMemoryObjectStorage(),
        )

        async def resolve_service(principal):
            return service

        async def authorize(principal, resource_type, resource_id, action):
            return True

        app = create_platform_app(
            identity=self.identity,
            identity_repository=FakeIdentityRepository(),
            knowledge=self.knowledge,
            memory=self.memory,
            redis=FakeRedis(),
            allowed_origins=("https://console.example.com",),
            session_service_resolver=resolve_service,
            session_authorizer=authorize,
        )
        client = TestClient(app, base_url="https://testserver")
        uploaded = client.post(
            "/api/platform/v1/files",
            content=b"tenant-a secret",
            headers={
                "Authorization": "Bearer valid-session",
                "Content-Type": "text/plain",
                "X-Filename": "secret.txt",
            },
        )
        self.assertEqual(uploaded.status_code, 201)
        asset_id = uploaded.json()["asset_id"]

        same_tenant = client.get(
            f"/api/platform/v1/files/{asset_id}",
            headers={"Authorization": "Bearer valid-session"},
        )
        self.assertEqual(same_tenant.status_code, 200)
        self.assertEqual(same_tenant.content, b"tenant-a secret")

        other_tenant = client.get(
            f"/api/platform/v1/files/{asset_id}",
            headers={"Authorization": "Bearer valid-session-b"},
        )
        self.assertEqual(other_tenant.status_code, 404)

    def test_factory_rejects_wildcard_and_non_https_origins(self):
        arguments = {
            "identity": self.identity,
            "identity_repository": FakeIdentityRepository(),
            "knowledge": self.knowledge,
            "memory": self.memory,
            "redis": FakeRedis(),
        }
        with self.assertRaises(ValueError):
            create_platform_app(**arguments, allowed_origins=("https://*.example.com",))
        with self.assertRaises(ValueError):
            create_platform_app(**arguments, allowed_origins=("http://console.example.com",))


if __name__ == "__main__":
    unittest.main()
