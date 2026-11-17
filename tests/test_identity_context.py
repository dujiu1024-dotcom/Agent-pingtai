"""Identity context adapter tests."""
import unittest

from starlette.requests import Request

from agent_platform.api.identity_context import IdentityContextResolver
from agent_platform.identity.models import (
    AuthenticatedApiCredential,
    AuthorizationContext,
    Principal,
)


class FakeIdentityRepository:
    async def get_authorization_context(self, *, tenant_id, user_id):
        if tenant_id != "tenant-a" or user_id != "user-a":
            return None
        return AuthorizationContext(
            tenant_id=tenant_id,
            user_id=user_id,
            tenant_roles=frozenset({"tenant_admin"}),
            effective_role_ids=frozenset({"tenant_admin"}),
            tenant_permissions=frozenset({"project.read", "knowledge.manage", "knowledge.read", "memory.read"}),
            project_ids=frozenset({"project-a"}),
            knowledge_manage_project_ids=frozenset({"project-a"}),
            memory_manage_project_ids=frozenset({"project-a"}),
            agent_ids=frozenset({"agent-a"}),
            memory_manage_agent_ids=frozenset({"agent-a"}),
        )


def make_request(principal=None, headers=None):
    state = {}
    if principal is not None:
        state["identity.principal"] = principal
    return Request({
        "type": "http",
        "method": "GET",
        "path": "/api/platform/v1/knowledge/search",
        "headers": headers or [],
        "state": state,
    })


class IdentityContextResolverTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.resolver = IdentityContextResolver(FakeIdentityRepository())

    async def test_knowledge_principal_comes_from_authenticated_identity_and_rbac(self):
        principal = Principal(
            user_id="user-a", tenant_id="tenant-a", session_id="session-a",
            email="a@example.com", display_name="A",
        )
        request = make_request(
            principal,
            headers=[(b"x-tenant", b"tenant-b"), (b"x-user", b"user-b")],
        )
        resolved = await self.resolver.knowledge(request)
        self.assertEqual(resolved.tenant_id, "tenant-a")
        self.assertEqual(resolved.user_id, "user-a")
        self.assertEqual(resolved.project_ids, frozenset({"project-a"}))
        self.assertEqual(resolved.roles, frozenset({"tenant_admin"}))
        self.assertEqual(
            resolved.permissions,
            frozenset({"project.read", "knowledge.manage", "knowledge.read", "memory.read"}),
        )

    async def test_memory_principal_includes_only_granted_agents(self):
        principal = Principal(
            user_id="user-a", tenant_id="tenant-a", session_id="session-a",
            email="a@example.com", display_name="A",
        )
        resolved = await self.resolver.memory(make_request(principal))
        self.assertEqual(resolved.agent_ids, frozenset({"agent-a"}))
        self.assertEqual(resolved.project_ids, frozenset({"project-a"}))
        self.assertEqual(resolved.manage_project_ids, frozenset({"project-a"}))
        self.assertEqual(resolved.manage_agent_ids, frozenset({"agent-a"}))
        self.assertEqual(
            resolved.permissions,
            frozenset({"project.read", "knowledge.manage"}),
        )

    async def test_api_credential_is_narrowed_to_scope_and_rbac_intersection(self):
        credential = AuthenticatedApiCredential(
            credential_id="credential-a",
            tenant_id="tenant-a",
            user_id="user-a",
            scopes=("project.read", "knowledge.read"),
        )
        resolved = await self.resolver.knowledge(make_request(credential))
        self.assertEqual(resolved.permissions, frozenset({"project.read", "knowledge.read"}))
        self.assertEqual(resolved.manage_project_ids, frozenset())

    async def test_api_credential_without_matching_permission_is_denied(self):
        credential = AuthenticatedApiCredential(
            credential_id="credential-a",
            tenant_id="tenant-a",
            user_id="user-a",
            scopes=("admin.root",),
        )
        with self.assertRaises(Exception) as raised:
            await self.resolver.knowledge(make_request(credential))
        self.assertEqual(getattr(raised.exception, "status_code", None), 403)

    async def test_missing_identity_is_rejected(self):
        with self.assertRaises(Exception) as raised:
            await self.resolver.knowledge(make_request())
        self.assertEqual(getattr(raised.exception, "status_code", None), 401)

    async def test_inactive_membership_is_rejected(self):
        principal = Principal(
            user_id="user-b", tenant_id="tenant-b", session_id="session-b",
            email="b@example.com", display_name="B",
        )
        with self.assertRaises(Exception) as raised:
            await self.resolver.memory(make_request(principal))
        self.assertEqual(getattr(raised.exception, "status_code", None), 403)


if __name__ == "__main__":
    unittest.main()
