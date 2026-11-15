from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.identity.models import (
    ApiCredentialRecord, InvitationRecord, ResourceRef, SessionRecord, UserRecord,
)
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
        self.api_credentials = {}
        self.invitations = {}

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
        record = self.sessions.get(token_hash)
        if record is None:
            return None
        membership_status = (
            "active" if (record.tenant_id, record.user_id) in self.memberships
            else "suspended"
        )
        return SessionRecord(
            session_id=record.session_id, tenant_id=record.tenant_id,
            user_id=record.user_id, email=record.email,
            display_name=record.display_name, expires_at=record.expires_at,
            revoked_at=record.revoked_at, user_status=self.user.status,
            membership_status=membership_status,
        )

    async def touch_session(self, *, session_id, tenant_id):
        self.touched.append((session_id, tenant_id))
        return any(
            record.session_id == session_id and record.tenant_id == tenant_id
            and (tenant_id, record.user_id) in self.memberships
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


    async def create_api_credential(
        self, *, credential_id, tenant_id, user_id, name,
        token_hash, scopes, expires_at,
    ):
        self.api_credentials[token_hash] = ApiCredentialRecord(
            credential_id=credential_id, tenant_id=tenant_id, user_id=user_id,
            name=name, scopes=scopes, expires_at=expires_at, revoked_at=None,
            user_status=self.user.status, membership_status="active",
        )

    async def get_api_credential(self, token_hash):
        return self.api_credentials.get(token_hash)

    async def touch_api_credential(self, *, credential_id, tenant_id):
        return any(
            record.credential_id == credential_id and record.tenant_id == tenant_id
            and record.revoked_at is None
            and (record.expires_at is None or record.expires_at > datetime.now(timezone.utc))
            and (tenant_id, record.user_id) in self.memberships
            and self.user.status == "active"
            for record in self.api_credentials.values()
        )

    async def revoke_api_credential(self, *, credential_id, tenant_id, user_id):
        for key, record in list(self.api_credentials.items()):
            if (record.credential_id, record.tenant_id, record.user_id) == (
                credential_id, tenant_id, user_id
            ):
                self.api_credentials[key] = ApiCredentialRecord(
                    credential_id=record.credential_id, tenant_id=record.tenant_id,
                    user_id=record.user_id, name=record.name, scopes=record.scopes,
                    expires_at=record.expires_at, revoked_at=datetime.now(timezone.utc),
                    user_status=record.user_status, membership_status=record.membership_status,
                )
                return True
        return False


    async def rotate_api_credential(
        self, *, old_credential_id, new_credential_id, tenant_id, user_id,
        name, token_hash, scopes, expires_at,
    ):
        old_key = next(
            (key for key, value in self.api_credentials.items()
             if value.credential_id == old_credential_id
             and value.tenant_id == tenant_id and value.user_id == user_id
             and value.revoked_at is None),
            None,
        )
        if old_key is None or (tenant_id, user_id) not in self.memberships:
            return False
        old = self.api_credentials[old_key]
        self.api_credentials[old_key] = ApiCredentialRecord(
            credential_id=old.credential_id, tenant_id=old.tenant_id,
            user_id=old.user_id, name=old.name, scopes=old.scopes,
            expires_at=old.expires_at, revoked_at=datetime.now(timezone.utc),
            user_status=old.user_status, membership_status=old.membership_status,
        )
        self.api_credentials[token_hash] = ApiCredentialRecord(
            credential_id=new_credential_id, tenant_id=tenant_id, user_id=user_id,
            name=name, scopes=scopes, expires_at=expires_at, revoked_at=None,
            user_status=self.user.status, membership_status="active",
        )
        return True

    async def create_invitation(
        self, *, invitation_id, tenant_id, email_normalized, role_id,
        token_hash, invited_by, expires_at,
    ):
        self.invitations[token_hash] = InvitationRecord(
            invitation_id=invitation_id, tenant_id=tenant_id,
            email_normalized=email_normalized, role_id=role_id,
            expires_at=expires_at, accepted_at=None,
        )

    async def get_invitation(self, token_hash):
        return self.invitations.get(token_hash)

    async def accept_invitation(
        self, *, invitation_id, token_hash, tenant_id, user_id, email_normalized,
    ):
        record = self.invitations.get(token_hash)
        if (
            record is None or record.invitation_id != invitation_id
            or record.tenant_id != tenant_id
            or record.email_normalized != email_normalized
            or record.accepted_at is not None
            or record.expires_at <= datetime.now(timezone.utc)
        ):
            return False
        self.invitations[token_hash] = InvitationRecord(
            invitation_id=record.invitation_id, tenant_id=record.tenant_id,
            email_normalized=record.email_normalized, role_id=record.role_id,
            expires_at=record.expires_at, accepted_at=datetime.now(timezone.utc),
        )
        self.memberships.add((tenant_id, user_id))
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

    def test_verifier_rejects_untrusted_scrypt_cost_parameters(self):
        encoded = PasswordHasher.hash("correct horse battery staple")
        tampered = encoded.replace("32768", "2147483648", 1)
        self.assertFalse(PasswordHasher.verify("correct horse battery staple", tampered))


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

    async def test_revoked_membership_invalidates_existing_session(self):
        issued = await self.service.login(
            email="admin@example.com", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        self.repo.memberships.remove(("tenant-a", "user-1"))
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



    async def test_api_credential_is_one_time_shown_and_revocable(self):
        issued = await self.service.login(
            email="admin@example.com", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        principal = await self.service.authenticate(issued.access_token)
        credential = await self.service.create_api_credential(
            principal=principal, name="automation", scopes=("project.read", "agent.run"),
        )
        self.assertNotIn(credential.access_token, self.repo.api_credentials)
        authenticated = await self.service.authenticate_api_credential(credential.access_token)
        self.assertEqual(authenticated.scopes, ("agent.run", "project.read"))
        self.repo.permissions.add(
            ("tenant-a", "user-1", "project", "project-1", "project.read")
        )
        await self.service.authorize_api_credential(
            credential=authenticated, resource=ResourceRef("project", "project-1"),
            permission_key="project.read",
        )
        with self.assertRaises(AuthorizationDenied):
            await self.service.authorize_api_credential(
                credential=authenticated, resource=ResourceRef("project", "project-1"),
                permission_key="tenant.manage",
            )
        self.assertTrue(await self.service.revoke_api_credential(
            principal=principal, credential_id=credential.credential_id,
        ))
        with self.assertRaises(AuthenticationError):
            await self.service.authenticate_api_credential(credential.access_token)



    async def test_api_credential_rotation_revokes_old_and_issues_new_atomically(self):
        issued = await self.service.login(
            email="admin@example.com", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        principal = await self.service.authenticate(issued.access_token)
        old = await self.service.create_api_credential(
            principal=principal, name="automation", scopes=("project.read",),
        )
        rotated = await self.service.rotate_api_credential(
            principal=principal, old_credential_id=old.credential_id,
            name="automation", scopes=("project.read",),
        )
        with self.assertRaises(AuthenticationError):
            await self.service.authenticate_api_credential(old.access_token)
        current = await self.service.authenticate_api_credential(rotated.access_token)
        self.assertEqual(current.credential_id, rotated.credential_id)

    async def test_invitation_requires_user_manage_and_is_email_bound(self):
        issued = await self.service.login(
            email="admin@example.com", password="correct horse battery staple",
            tenant_id="tenant-a",
        )
        principal = await self.service.authenticate(issued.access_token)
        with self.assertRaises(AuthorizationDenied):
            await self.service.create_invitation(
                principal=principal, email="new@example.com", role_id="viewer",
            )
        self.repo.permissions.add(
            ("tenant-a", "user-1", "tenant", "tenant-a", "user.manage")
        )
        invitation_id, token, _ = await self.service.create_invitation(
            principal=principal, email="admin@example.com", role_id="viewer",
        )
        self.assertTrue(await self.service.accept_invitation(
            principal=principal, invitation_token=token,
        ))
        with self.assertRaises(AuthenticationError):
            await self.service.accept_invitation(
                principal=principal, invitation_token=token,
            )

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
