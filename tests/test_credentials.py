from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.security.credentials import CredentialService
from agent_platform.security.secrets import SecretManager, SecretVersion


class FakeSecretBackend:
    def __init__(self):
        self.values = {}

    async def put(self, secret_ref, version, value):
        self.values[(secret_ref, version)] = value

    async def get(self, secret_ref, version):
        return self.values.get((secret_ref, version))

    async def revoke(self, secret_ref, version):
        self.values.pop((secret_ref, version), None)


class FakeSecretMetadata:
    def __init__(self):
        self.rows = {}

    async def list_versions(self, secret_ref):
        return [row for (ref, _), row in sorted(self.rows.items()) if ref == secret_ref]

    async def create_version(self, secret_ref, version, created_at):
        self.rows[(secret_ref, version)] = SecretVersion(secret_ref, version, "pending", created_at)

    async def activate_version(self, secret_ref, version):
        for key, row in list(self.rows.items()):
            if key[0] == secret_ref and row.status == "active":
                self.rows[key] = SecretVersion(secret_ref, row.version, "revoked", row.created_at)
        row = self.rows[(secret_ref, version)]
        self.rows[(secret_ref, version)] = SecretVersion(secret_ref, version, "active", row.created_at)

    async def revoke_version(self, secret_ref, version, revoked_at):
        row = self.rows[(secret_ref, version)]
        self.rows[(secret_ref, version)] = SecretVersion(secret_ref, version, "revoked", row.created_at)

    async def revoke_all(self, secret_ref, revoked_at):
        for key, row in list(self.rows.items()):
            if key[0] == secret_ref and row.status in {"active", "pending"}:
                self.rows[key] = SecretVersion(secret_ref, row.version, "revoked", row.created_at)
        return await self.list_pending_revocations(secret_ref)

    async def list_pending_revocations(self, secret_ref=None):
        return [row.version for (ref, _), row in self.rows.items()
                if ref == secret_ref and row.status in {"pending", "revoked"}
                and row.backend_revoked_at is None]

    async def mark_backend_revoked(self, secret_ref, version, revoked_at):
        row = self.rows[(secret_ref, version)]
        self.rows[(secret_ref, version)] = SecretVersion(
            secret_ref, version, "revoked", row.created_at, revoked_at
        )


class FakeCredentialRepository:
    def __init__(self):
        self.records = {}
        self.grants = {}

    async def create(self, record):
        self.records[(record.tenant_id, record.credential_id)] = record

    async def get(self, *, tenant_id, credential_id):
        return self.records.get((tenant_id, credential_id))

    async def grant(self, grant):
        self.grants[(grant.tenant_id, grant.credential_id,
                     grant.consumer_type, grant.consumer_id)] = grant

    async def get_grant(self, *, tenant_id, credential_id, consumer_type, consumer_id):
        return self.grants.get((tenant_id, credential_id, consumer_type, consumer_id))

    async def revoke(self, *, tenant_id, credential_id):
        from dataclasses import replace
        key = (tenant_id, credential_id)
        row = self.records.get(key)
        if row is None or row.revoked_at is not None:
            return False
        self.records[key] = replace(row, revoked_at=datetime.now(timezone.utc))
        self.grants = {key: value for key, value in self.grants.items()
                       if key[:2] != (tenant_id, credential_id)}
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
        self.backend = FakeSecretBackend()
        self.secret_manager = SecretManager(backend=self.backend, metadata=FakeSecretMetadata())
        self.repository = FakeCredentialRepository()
        self.audit = FakeAudit()
        self.service = CredentialService(
            secrets=self.secret_manager, repository=self.repository, audit=self.audit
        )

    async def test_create_and_reuse_by_agent_and_workflow_with_scope_subset(self):
        record = await self.service.create(
            tenant_id="tenant-a", name="Drive OAuth", credential_type="oauth",
            value="oauth-secret", scopes=("drive.read", "drive.write"), created_by="owner",
        )
        self.assertNotIn("oauth-secret", repr(record))
        self.assertEqual(len(self.backend.values), 1)
        await self.service.grant(
            tenant_id="tenant-a", credential_id=record.credential_id,
            consumer_type="agent", consumer_id="agent-1",
            scopes=("drive.read",), granted_by="owner",
        )
        await self.service.grant(
            tenant_id="tenant-a", credential_id=record.credential_id,
            consumer_type="workflow", consumer_id="workflow-1",
            scopes=("drive.read", "drive.write"), granted_by="owner",
        )
        self.assertEqual(await self.service.resolve(
            tenant_id="tenant-a", credential_id=record.credential_id,
            consumer_type="agent", consumer_id="agent-1",
            required_scopes=("drive.read",), actor_id="runtime",
        ), "oauth-secret")
        self.assertEqual(await self.service.resolve(
            tenant_id="tenant-a", credential_id=record.credential_id,
            consumer_type="workflow", consumer_id="workflow-1",
            required_scopes=("drive.write",), actor_id="runtime",
        ), "oauth-secret")
        self.assertEqual(
            [e["action"] for e in self.audit.events].count("credential.resolve"), 2
        )

    async def test_denies_cross_tenant_ungranted_and_scope_escalation(self):
        record = await self.service.create(
            tenant_id="tenant-a", name="API key", credential_type="api_key",
            value="secret", scopes=("issues.read",), created_by="owner",
        )
        with self.assertRaises(PermissionError):
            await self.service.resolve(
                tenant_id="tenant-b", credential_id=record.credential_id,
                consumer_type="agent", consumer_id="agent-1",
                required_scopes=("issues.read",), actor_id="runtime",
            )
        with self.assertRaises(PermissionError):
            await self.service.grant(
                tenant_id="tenant-a", credential_id=record.credential_id,
                consumer_type="agent", consumer_id="agent-1",
                scopes=("issues.write",), granted_by="owner",
            )
        await self.service.grant(
            tenant_id="tenant-a", credential_id=record.credential_id,
            consumer_type="agent", consumer_id="agent-1",
            scopes=("issues.read",), granted_by="owner",
        )
        with self.assertRaises(PermissionError):
            await self.service.resolve(
                tenant_id="tenant-a", credential_id=record.credential_id,
                consumer_type="agent", consumer_id="agent-1",
                required_scopes=("issues.write",), actor_id="runtime",
            )

    async def test_revoke_removes_grants_and_prevents_future_resolution(self):
        record = await self.service.create(
            tenant_id="tenant-a", name="Service Account", credential_type="service_account",
            value="service-token", scopes=("storage.read",), created_by="owner",
        )
        await self.service.grant(
            tenant_id="tenant-a", credential_id=record.credential_id,
            consumer_type="workflow", consumer_id="wf",
            scopes=("storage.read",), granted_by="owner",
        )
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a", credential_id=record.credential_id, actor_id="owner"
        ))
        self.assertFalse(self.backend.values)
        with self.assertRaises(PermissionError):
            await self.service.resolve(
                tenant_id="tenant-a", credential_id=record.credential_id,
                consumer_type="workflow", consumer_id="wf",
                required_scopes=("storage.read",), actor_id="runtime",
            )

    async def test_audit_failure_is_fail_closed_for_secret_release(self):
        record = await self.service.create(
            tenant_id="tenant-a", name="OAuth", credential_type="oauth",
            value="must-not-leak", scopes=("profile.read",), created_by="owner",
        )
        await self.service.grant(
            tenant_id="tenant-a", credential_id=record.credential_id,
            consumer_type="agent", consumer_id="agent",
            scopes=("profile.read",), granted_by="owner",
        )
        self.audit.fail = True
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await self.service.resolve(
                tenant_id="tenant-a", credential_id=record.credential_id,
                consumer_type="agent", consumer_id="agent",
                required_scopes=("profile.read",), actor_id="runtime",
            )

    async def test_invalid_credential_types_and_wildcard_scopes_rejected(self):
        with self.assertRaises(ValueError):
            await self.service.create(
                tenant_id="tenant-a", name="bad", credential_type="password",
                value="secret", scopes=("read",), created_by="owner",
            )
        with self.assertRaises(ValueError):
            await self.service.create(
                tenant_id="tenant-a", name="bad", credential_type="api_key",
                value="secret", scopes=("*",), created_by="owner",
            )


if __name__ == "__main__":
    unittest.main()
