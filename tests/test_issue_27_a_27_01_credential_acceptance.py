from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import datetime, timezone

from agent_platform.security.credentials import CredentialGrant, CredentialRecord, CredentialService
from agent_platform.security.secrets import SecretManager, SecretVersion
from agent_platform.security.vault_kv_v2 import VaultKvV2SecretBackend


class MemorySecretMetadata:
    def __init__(self):
        self.rows = {}

    async def list_versions(self, secret_ref):
        return [row for (ref, _), row in sorted(self.rows.items()) if ref == secret_ref]

    async def create_version(self, secret_ref, version, created_at):
        key = (secret_ref, version)
        if key in self.rows:
            raise ValueError("duplicate version")
        self.rows[key] = SecretVersion(secret_ref, version, "pending", created_at)

    async def activate_version(self, secret_ref, version):
        for key, row in list(self.rows.items()):
            if key[0] == secret_ref and row.status == "active":
                self.rows[key] = replace(row, status="revoked")
        key = (secret_ref, version)
        self.rows[key] = replace(self.rows[key], status="active")

    async def revoke_version(self, secret_ref, version, revoked_at):
        key = (secret_ref, version)
        if key in self.rows:
            self.rows[key] = replace(self.rows[key], status="revoked")

    async def revoke_all(self, secret_ref, revoked_at):
        for key, row in list(self.rows.items()):
            if key[0] == secret_ref and row.status in {"active", "pending"}:
                self.rows[key] = replace(row, status="revoked")
        return await self.list_pending_revocations(secret_ref)

    async def list_pending_revocations(self, secret_ref=None):
        return [
            row.version for (ref, _), row in self.rows.items()
            if ref == secret_ref and row.status in {"pending", "revoked"}
            and row.backend_revoked_at is None
        ]

    async def mark_backend_revoked(self, secret_ref, version, revoked_at):
        key = (secret_ref, version)
        self.rows[key] = replace(
            self.rows[key], status="revoked", backend_revoked_at=revoked_at
        )


class MemoryCredentialRepository:
    def __init__(self):
        self.records = {}
        self.grants = {}

    async def create(self, record: CredentialRecord):
        self.records[(record.tenant_id, record.credential_id)] = record

    async def get(self, *, tenant_id, credential_id):
        return self.records.get((tenant_id, credential_id))

    async def grant(self, grant: CredentialGrant):
        self.grants[(grant.tenant_id, grant.credential_id,
                     grant.consumer_type, grant.consumer_id)] = grant

    async def get_grant(self, *, tenant_id, credential_id, consumer_type, consumer_id):
        return self.grants.get((tenant_id, credential_id, consumer_type, consumer_id))

    async def revoke_grant(self, *, tenant_id, credential_id, consumer_type, consumer_id):
        self.grants.pop((tenant_id, credential_id, consumer_type, consumer_id), None)

    async def revoke(self, *, tenant_id, credential_id):
        key = (tenant_id, credential_id)
        record = self.records.get(key)
        if record is None or record.revoked_at is not None:
            return False
        self.records[key] = replace(record, revoked_at=datetime.now(timezone.utc))
        self.grants = {
            grant_key: grant for grant_key, grant in self.grants.items()
            if grant_key[:2] != key
        }
        return True


class FakeVaultHttp:
    def __init__(self):
        self.calls = []
        self.values = {}

    def request(self, method, url, headers, body, timeout):
        self.calls.append((method, url, headers, body, timeout))
        if method == "POST" and "/data/" in url:
            self.values[url] = json.loads(body)["data"]["value"]
            return 200, b'{"data":{"version":1}}'
        if method == "GET" and "/data/" in url:
            if url not in self.values:
                return 404, b"{}"
            payload = {"data": {"data": {"value": self.values[url]}}}
            return 200, json.dumps(payload).encode()
        if method == "DELETE" and "/metadata/" in url:
            data_url = url.replace("/metadata/", "/data/")
            self.values.pop(data_url, None)
            return 204, b""
        return 404, b"{}"


class RecordingAudit:
    def __init__(self):
        self.events = []
        self.fail_resolve = False

    async def record(self, **event):
        self.events.append(event)
        if event.get("action") == "credential.resolve" and self.fail_resolve:
            raise RuntimeError("audit unavailable")


class CredentialAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.http = FakeVaultHttp()
        self.vault = VaultKvV2SecretBackend(
            address="https://vault.example",
            token="test-vault-token",
            mount="platform-secrets",
            request_func=self.http.request,
        )
        self.secret_metadata = MemorySecretMetadata()
        self.secret_manager = SecretManager(
            backend=self.vault, metadata=self.secret_metadata
        )
        self.repository = MemoryCredentialRepository()
        self.audit = RecordingAudit()
        self.service = CredentialService(
            secrets=self.secret_manager, repository=self.repository, audit=self.audit
        )

    async def _create_and_grant(self):
        record = await self.service.create(
            tenant_id="tenant-a",
            name="Drive OAuth",
            credential_type="oauth",
            value="sensitive-oauth-token",
            scopes=("drive.read", "drive.write"),
            created_by="owner",
        )
        await self.service.grant(
            tenant_id="tenant-a",
            credential_id=record.credential_id,
            consumer_type="agent",
            consumer_id="agent-1",
            scopes=("drive.read",),
            granted_by="owner",
        )
        return record

    async def test_secret_is_vault_backed_and_metadata_contains_only_reference(self):
        record = await self._create_and_grant()
        write = next(call for call in self.http.calls if call[0] == "POST")
        self.assertTrue(write[1].startswith("https://vault.example/v1/platform-secrets/data/"))
        self.assertEqual(json.loads(write[3])["data"]["value"], "sensitive-oauth-token")
        self.assertEqual(len(self.secret_metadata.rows), 1)
        self.assertNotIn("sensitive-oauth-token", repr(record))
        self.assertNotIn("sensitive-oauth-token", repr(self.secret_metadata.rows))
        self.assertNotIn("sensitive-oauth-token", repr(self.repository.records))
        self.assertEqual(record.secret_ref, next(iter(self.secret_metadata.rows))[0])

    async def test_runtime_plaintext_release_fails_closed_when_resolve_audit_fails(self):
        record = await self._create_and_grant()
        self.audit.fail_resolve = True
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await self.service.resolve(
                tenant_id="tenant-a",
                credential_id=record.credential_id,
                consumer_type="agent",
                consumer_id="agent-1",
                required_scopes=("drive.read",),
                actor_id="runtime-worker",
            )
        resolve_events = [
            event for event in self.audit.events
            if event.get("action") == "credential.resolve"
        ]
        self.assertEqual(len(resolve_events), 1)
        self.assertNotIn("sensitive-oauth-token", repr(resolve_events))

    async def test_revocation_removes_all_vault_versions_and_consumer_grants(self):
        record = await self._create_and_grant()
        await self.secret_manager.rotate(
            secret_ref=record.secret_ref, value="rotated-oauth-token"
        )
        self.assertTrue(self.http.values)
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a",
            credential_id=record.credential_id,
            actor_id="owner",
        ))
        self.assertFalse(self.http.values)
        self.assertFalse(self.repository.grants)
        with self.assertRaises(PermissionError):
            await self.service.resolve(
                tenant_id="tenant-a",
                credential_id=record.credential_id,
                consumer_type="agent",
                consumer_id="agent-1",
                required_scopes=("drive.read",),
                actor_id="runtime-worker",
            )
        revoked_versions = await self.secret_manager.describe(secret_ref=record.secret_ref)
        self.assertTrue(all(version.status == "revoked" for version in revoked_versions))


if __name__ == "__main__":
    unittest.main()
