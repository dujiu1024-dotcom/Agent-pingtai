from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.identity.models import ResourceRef, SessionRecord, UserRecord
from agent_platform.identity.security import CredentialError, PasswordHasher, token_digest
from agent_platform.identity.service import AuthenticationError, AuthorizationDenied, IdentityService


class FakeIdentityRepository:
    def __init__(self, user: UserRecord):
        self.user = user
        self.memberships = {("tenant-a", user.user_id)}
        self.sessions = {}
        self.permissions = set()
        self.revoked = set()
        self.touched = []
        self.grants = []
        self.revokes = []

    async def get_user_by_email(self, normalized_email):
        return self.user if normalized_email == self.user.email.casefold() else None

    async def is_active_member(self, *, tenant_id, user_id):
        return (tenant_id, user_id) in self.memberships

    async def create_session(self, *, session_id, tenant_id, user_id, token_hash, expires_at):
        if (tenant_id, user_id) not in self.memberships:
            raise PermissionError("active tenant membership required")
        self.sessions[token_hash] = SessionRecord(
            session_id=session_id, tenant_id=tenant_id, user_id=user_id,
            email=self.user.email, display_name=self.user.display_name,
            expires_at=expires_at, revoked_at=None, user_status=self.user.status,
            membership_status="active",
        )

    async def get_session_by_token_hash(self, token_hash):
        return self.sessions.get(token_hash)

    async def touch_session(self, *, session_id, tenant_id):
        self.touched.append((session_id, tenant_id))
        return any(
            record.session_id == session_id and record.tenant_id == tenant_id
            and record.revoked_at is None
            and record.expires_at > datetime.now(timezone.utc)
            for record in self.sessions.values()
        )

    async def revoke_session(self, *, session_id, tenant_id, user_id, revoked_at):
        for key, record in list(self.sessions.items()):
            if (record.session_id, record.tenant_id, record.user_id) == (session_id, tenant_id, user_id):
                self.sessions[key] = SessionRecord(
                    session_id=record.session_id, tenant_id=record.tenant_id,
                    user_id=record.user_id, email=record.email,
                    display_name=record.display_name, expires_at=record.expires_at,
                    revoked_at=revoked_at, user_status=record.user_status,
                    membership_status=record.membership_status,
                )
                return True
        return False

    async def has_permission(self, *, tenant_id, user_id, resource_type, resource_id, permission_key):
        return (tenant_id, user_id, resource_type, resource_id, permission_key) in self.permissions

    async def grant_role(self, **kwargs):
        self.grants.append(kwargs)
        return "binding-1"

    async def revoke_role(self, **kwargs):
        self.revokes.append(kwargs)
        return True


class PasswordSecurityTests(unittest.TestCase):
    def test_password_hash_is_salted_and_verifiable(self):
        first = PasswordHasher.hash("correct horse battery staple")
        second = PasswordHasher.hash("correct horse battery staple")
        self.assertNotEqual(first, second)
        self.assertTrue(PasswordHasher.verify("correct horse battery staple", first))
        self.assertFalse(PasswordHasher.verify("wrong password", first))

    def test_password_policy_rejects_short_password(self):
        with self.assertRaises(CredentialError):
            PasswordHasher.hash("short")

    def test_token_digest_is_one_way_and_stable(self):
        token = "aps_random-high-entropy-token"
        self.assertEqual(token_digest(token), token_digest(token))
        self.assertNotEqual(token_digest(token), token)


class IdentityServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.password_hash = PasswordHasher.hash("correct horse battery staple")
        self.user = UserRecord(
            user_id="user-1", email="admin@example.com", display_name="Admin",
            password_hash=self.password_hash, status="active",
        )
        self.repo = FakeIdentityRepository(self.user)
        self.service = IdentityService(self.repo)

    async def test_login_stores_only_token_hash_and_authenticates_tenant_principal(self):
        issued = await self.service.login(
            email=" ADMIN@EXAMPLE.COM ", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        self.assertNotIn(issued.access_token, self.repo.sessions)
        self.assertIn(token_digest(issued.access_token), self.repo.sessions)
        principal = await self.service.authenticate(issued.access_token)
        self.assertEqual(principal.user_id, "user-1")
        self.assertEqual(principal.tenant_id, "tenant-a")
        self.assertEqual(principal.session_id, issued.session_id)
        self.assertEqual(len(self.repo.touched), 1)

    async def test_login_rejects_wrong_password_unknown_user_and_nonmember(self):
        with self.assertRaises(AuthenticationError):
            await self.service.login(
                email="admin@example.com", password="incorrect password",
                tenant_id="tenant-a",
            )
        with self.assertRaises(AuthenticationError):
            await self.service.login(
                email="unknown@example.com", password="correct horse battery staple",
                tenant_id="tenant-a",
            )
        with self.assertRaises(AuthenticationError):
            await self.service.login(
                email="admin@example.com", password="correct horse battery staple",
                tenant_id="tenant-b",
            )

    async def test_logout_revokes_session_immediately(self):
        issued = await self.service.login(
            email="admin@example.com", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        principal = await self.service.authenticate(issued.access_token)
        self.assertTrue(await self.service.logout(principal))
        with self.assertRaises(AuthenticationError):
            await self.service.authenticate(issued.access_token)

    async def test_suspended_user_is_rejected_on_every_request(self):
        issued = await self.service.login(
            email="admin@example.com", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        self.repo.user = UserRecord(
            user_id="user-1", email="admin@example.com", display_name="Admin",
            password_hash=self.password_hash, status="suspended",
        )
        with self.assertRaises(AuthenticationError):
            await self.service.authenticate(issued.access_token)

    async def test_authorization_is_default_deny_and_tenant_scoped(self):
        issued = await self.service.login(
            email="admin@example.com", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        principal = await self.service.authenticate(issued.access_token)
        resource = ResourceRef("project", "project-1")
        with self.assertRaises(AuthorizationDenied):
            await self.service.authorize(
                principal=principal, resource=resource,
                permission_key="project.read",
            )
        self.repo.permissions.add(
            ("tenant-a", "user-1", "project", "project-1", "project.read")
        )
        await self.service.authorize(
            principal=principal, resource=resource,
            permission_key="project.read",
        )
        other_tenant = ResourceRef("project", "project-1")
        principal_other = type(principal)(
            user_id=principal.user_id, tenant_id="tenant-b",
            session_id=principal.session_id, email=principal.email,
            display_name=principal.display_name,
        )
        with self.assertRaises(AuthorizationDenied):
            await self.service.authorize(
                principal=principal_other, resource=other_tenant,
                permission_key="project.read",
            )

    async def test_role_changes_require_permission_and_use_audited_repository_port(self):
        issued = await self.service.login(
            email="admin@example.com", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        principal = await self.service.authenticate(issued.access_token)
        resource = ResourceRef("tenant", "tenant-a")
        with self.assertRaises(AuthorizationDenied):
            await self.service.grant_role(
                principal=principal, subject_type="user", subject_id="user-2",
                resource=resource, role_id="viewer",
            )
        self.repo.permissions.add(
            ("tenant-a", "user-1", "tenant", "tenant-a", "identity.role.manage")
        )
        binding_id = await self.service.grant_role(
            principal=principal, subject_type="user", subject_id="user-2",
            resource=resource, role_id="viewer",
        )
        self.assertEqual(binding_id, "binding-1")
        self.assertEqual(self.repo.grants[0]["actor_id"], "user-1")
        self.assertTrue(await self.service.revoke_role(
            principal=principal, binding_id=binding_id, resource=resource
        ))
        self.assertEqual(self.repo.revokes[0]["tenant_id"], "tenant-a")
        self.assertEqual(self.repo.revokes[0]["resource_type"], "tenant")


if __name__ == "__main__":
    unittest.main()
