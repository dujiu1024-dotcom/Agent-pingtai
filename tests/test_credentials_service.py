from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.credentials.service import (
    Credential, CredentialGrant, CredentialService, CredentialError,
)


class FakeSecretManager:
    def __init__(self):
        self.values = {}
        self.calls = []

    async def create(self, *, secret_ref, value):
        self.calls.append(("create", secret_ref))
        if secret_ref in self.values:
            raise ValueError("duplicate secret")
        self.values[secret_ref] = value

    async def resolve(self, *, secret_ref):
        self.calls.append(("resolve", secret_ref))
        return self.values.get(secret_ref)

    async def revoke(self, *, secret_ref):
        self.calls.append(("revoke", secret_ref))
        self.values.pop(secret_ref, None)


class FakeCredentialRepository:
    def __init__(self):
        self.credentials = {}
        self.grant_rows = {}

    async def create(self, item):
        key = (item.tenant_id, item.credential_id)
        if key in self.credentials:
            raise ValueError("duplicate credential")
        self.credentials[key] = item

    async def get(self, tenant_id, credential_id):
        return self.credentials.get((tenant_id, credential_id))

    async def list(self, tenant_id, *, limit=100):
        return [v for (t, _), v in self.credentials.items() if t == tenant_id][:limit]

    async def grant(self, item):
        key = (item.tenant_id, item.credential_id, item.grantee_type, item.grantee_id)
        self.grant_rows[key] = item

    async def list_grants(self, tenant_id, credential_id):
        return [v for k, v in self.grant_rows.items() if k[:2] == (tenant_id, credential_id)]

    async def get_grant(self, tenant_id, credential_id, grantee_type, grantee_id):
        return self.grant_rows.get((tenant_id, credential_id, grantee_type, grantee_id))

    async def revoke(self, tenant_id, credential_id, at):
        key = (tenant_id, credential_id)
        item = self.credentials.get(key)
        if item is None or item.status != "active":
            return False
        from dataclasses import replace
        self.credentials[key] = replace(item, status="revoked", revoked_at=at)
        for grant_key, grant in list(self.grant_rows.items()):
            if grant_key[:2] == key:
                self.grant_rows[grant_key] = replace(grant, revoked_at=at)
        return True


class FakeAudit:
    def __init__(self):
        self.events = []
        self.fail = False

    async def record(self, **event):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


class CredentialServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = FakeCredentialRepository()
        self.secrets = FakeSecretManager()
        self.audit = FakeAudit()
        self.service = CredentialService(
            repository=self.repository, secret_manager=self.secrets, audit_writer=self.audit,
        )

    async def create(self):
        return await self.service.create(
            tenant_id="tenant-a", credential_id="drive-main", provider="google_drive",
            display_name="Drive production", value="private-token",
            scopes=("files.read", "files.write"), actor_id="user-a",
        )

    async def test_secret_value_never_appears_in_metadata_or_api_record(self):
        item = await self.create()
        self.assertEqual(item.secret_ref, "secret://tenant-a/credentials/drive-main")
        self.assertNotIn("private-token", repr(item))
        self.assertNotIn("private-token", repr(self.audit.events))
        self.assertEqual(self.secrets.values[item.secret_ref], "private-token")

    async def test_grant_is_limited_to_declared_labels_and_agent_workflow(self):
        await self.create()
        with self.assertRaises(CredentialError):
            await self.service.grant(
                tenant_id="tenant-a", credential_id="drive-main", grantee_type="agent",
                grantee_id="agent-a", scopes=("admin",), actor_id="user-a",
            )
        with self.assertRaises(CredentialError):
            await self.service.grant(
                tenant_id="tenant-a", credential_id="drive-main", grantee_type="user",
                grantee_id="user-a", scopes=("files.read",), actor_id="user-a",
            )
        grant = await self.service.grant(
            tenant_id="tenant-a", credential_id="drive-main", grantee_type="workflow",
            grantee_id="workflow-a", scopes=("files.read",), actor_id="user-a",
        )
        self.assertEqual(grant.scopes, ("files.read",))

    async def test_runtime_resolution_requires_explicit_grant_and_audits_before_read(self):
        item = await self.create()
        with self.assertRaises(PermissionError):
            await self.service.resolve_for_runtime(
                tenant_id="tenant-a", credential_id="drive-main", grantee_type="agent",
                grantee_id="agent-a", required_scopes=("files.read",),
            )
        self.assertNotIn(("resolve", item.secret_ref), self.secrets.calls)
        await self.service.grant(
            tenant_id="tenant-a", credential_id="drive-main", grantee_type="agent",
            grantee_id="agent-a", scopes=("files.read",), actor_id="user-a",
        )
        self.assertEqual(await self.service.resolve_for_runtime(
            tenant_id="tenant-a", credential_id="drive-main", grantee_type="agent",
            grantee_id="agent-a", required_scopes=("files.read",),
        ), "private-token")
        self.assertEqual(self.audit.events[-1]["action"], "credential.resolve")

    async def test_runtime_scope_subset_and_tenant_isolation(self):
        await self.create()
        await self.service.grant(
            tenant_id="tenant-a", credential_id="drive-main", grantee_type="agent",
            grantee_id="agent-a", scopes=("files.read",), actor_id="user-a",
        )
        with self.assertRaises(PermissionError):
            await self.service.resolve_for_runtime(
                tenant_id="tenant-a", credential_id="drive-main", grantee_type="agent",
                grantee_id="agent-a", required_scopes=("files.write",),
            )
        with self.assertRaises(KeyError):
            await self.service.resolve_for_runtime(
                tenant_id="tenant-b", credential_id="drive-main", grantee_type="agent",
                grantee_id="agent-a", required_scopes=("files.read",),
            )

    async def test_revoke_disables_all_grants_before_secret_cleanup(self):
        item = await self.create()
        await self.service.grant(
            tenant_id="tenant-a", credential_id="drive-main", grantee_type="agent",
            grantee_id="agent-a", scopes=("files.read",), actor_id="user-a",
        )
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a", credential_id="drive-main", actor_id="user-a",
        ))
        self.assertNotIn(item.secret_ref, self.secrets.values)
        with self.assertRaises(KeyError):
            await self.service.resolve_for_runtime(
                tenant_id="tenant-a", credential_id="drive-main", grantee_type="agent",
                grantee_id="agent-a", required_scopes=("files.read",),
            )

    async def test_audit_failure_fails_closed_before_secret_write(self):
        self.audit.fail = True
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await self.create()
        self.assertEqual(self.secrets.calls, [])
        self.assertEqual(self.repository.credentials, {})

    async def test_metadata_failure_compensates_secret_write(self):
        original = self.repository.create
        async def fail_create(item):
            raise RuntimeError("metadata unavailable")
        self.repository.create = fail_create
        with self.assertRaisesRegex(RuntimeError, "metadata unavailable"):
            await self.create()
        self.assertEqual(self.secrets.values, {})
        self.repository.create = original

    async def test_listing_is_tenant_scoped_and_bounded(self):
        await self.create()
        self.assertEqual(len(await self.service.list(tenant_id="tenant-a")), 1)
        self.assertEqual(await self.service.list(tenant_id="tenant-b"), [])
        with self.assertRaises(CredentialError):
            await self.service.list(tenant_id="tenant-a", limit=201)


if __name__ == "__main__":
    unittest.main()
