"""Unit tests for independent machine identity credentials."""
import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.identity.models import ResourceRef
from agent_platform.identity.service_accounts import (
    ServiceIdentityError, ServiceIdentityService, _Credential,
)


class FakeServiceIdentityRepository:
    def __init__(self):
        self.record = None
        self.revoked = False
        self.allowed = True

    async def issue(self, **kwargs):
        self.record = _Credential(
            credential_id=kwargs["credential_id"],
            tenant_id=kwargs["tenant_id"],
            service_account_id=kwargs["service_account_id"],
            agent_id=kwargs["agent_id"],
            scopes=kwargs["scopes"],
            expires_at=kwargs["expires_at"],
            revoked_at=None,
            service_status="active",
            agent_status="active" if kwargs["agent_id"] else None,
        )

    async def get(self, token_hash):
        return None if self.revoked else self.record

    async def touch(self, *, credential_id, tenant_id):
        return bool(
            self.record and self.record.credential_id == credential_id
            and self.record.tenant_id == tenant_id and not self.revoked
        )

    async def revoke(self, *, credential_id, tenant_id, service_account_id):
        if not self.record or self.record.credential_id != credential_id:
            return False
        if self.record.tenant_id != tenant_id or self.record.service_account_id != service_account_id:
            return False
        self.revoked = True
        return True

    async def has_permission(self, *, principal, resource, permission_key):
        return self.allowed


class ServiceIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = FakeServiceIdentityRepository()
        self.service = ServiceIdentityService(self.repo)

    async def test_agent_credential_is_opaque_and_authenticates_agent_principal(self):
        issued = await self.service.issue_credential(
            tenant_id="tenant-a", service_account_id="worker",
            agent_id="agent-1", name="worker runtime",
            scopes=("agent.run", " blackboard.read ", "agent.run"),
        )
        self.assertTrue(issued.token.startswith("svc_"))
        self.assertEqual(issued.scopes, ("agent.run", "blackboard.read"))
        principal = await self.service.authenticate(issued.token)
        self.assertEqual(principal.tenant_id, "tenant-a")
        self.assertEqual(principal.service_account_id, "worker")
        self.assertEqual(principal.agent_id, "agent-1")
        self.assertNotIn(issued.token, repr(self.repo.record))

    async def test_expired_or_revoked_credential_is_rejected(self):
        issued = await self.service.issue_credential(
            tenant_id="tenant-a", service_account_id="worker",
            name="worker", scopes=("agent.run",),
        )
        self.repo.record = _Credential(
            **{**self.repo.record.__dict__,
               "expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}
        )
        with self.assertRaises(ServiceIdentityError):
            await self.service.authenticate(issued.token)
        self.repo.record = _Credential(
            **{**self.repo.record.__dict__, "expires_at": None}
        )
        self.repo.revoked = True
        with self.assertRaises(ServiceIdentityError):
            await self.service.authenticate(issued.token)

    async def test_authorization_requires_both_scope_and_rbac(self):
        issued = await self.service.issue_credential(
            tenant_id="tenant-a", service_account_id="worker",
            name="worker", scopes=("agent.run",),
        )
        principal = await self.service.authenticate(issued.token)
        await self.service.authorize(
            principal=principal, resource=ResourceRef("agent", "agent-1"),
            permission_key="agent.run",
        )
        with self.assertRaises(ServiceIdentityError):
            await self.service.authorize(
                principal=principal, resource=ResourceRef("agent", "agent-1"),
                permission_key="identity.role.manage",
            )
        self.repo.allowed = False
        with self.assertRaises(ServiceIdentityError):
            await self.service.authorize(
                principal=principal, resource=ResourceRef("agent", "agent-1"),
                permission_key="agent.run",
            )

    async def test_revocation_is_tenant_and_service_account_scoped(self):
        issued = await self.service.issue_credential(
            tenant_id="tenant-a", service_account_id="worker",
            name="worker", scopes=("agent.run",),
        )
        self.assertFalse(await self.service.revoke(
            tenant_id="tenant-b", service_account_id="worker",
            credential_id=issued.credential_id,
        ))
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a", service_account_id="worker",
            credential_id=issued.credential_id,
        ))
        with self.assertRaises(ServiceIdentityError):
            await self.service.authenticate(issued.token)


if __name__ == "__main__":
    unittest.main()
