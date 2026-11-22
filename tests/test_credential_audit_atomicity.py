from __future__ import annotations

import copy
import unittest
from datetime import datetime, timezone

from agent_platform.security.credentials import CredentialService, PostgresCredentialRepository
from agent_platform.security.secrets import SecretManager, SecretVersion


class _Cursor:
    def __init__(self, row=None):
        self.row = row

    async def fetchone(self):
        return self.row


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.snapshot = copy.deepcopy((
            self.connection.credentials,
            self.connection.grants,
        ))
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        if exc_type is not None:
            self.connection.credentials, self.connection.grants = self.snapshot
        return False


class _TransactionalConnection:
    """Small transaction-aware Postgres fake for credential mutation rollback."""

    def __init__(self):
        self.credentials = {}
        self.grants = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return _Transaction(self)

    async def execute(self, query, params=()):
        if "INSERT INTO platform_credentials" in query:
            credential_id, tenant_id, name, credential_type, secret_ref, scopes, created_by, created_at = params
            self.credentials[(tenant_id, credential_id)] = {
                "credential_id": credential_id, "tenant_id": tenant_id,
                "name": name, "credential_type": credential_type,
                "secret_ref": secret_ref, "scopes": scopes,
                "created_by": created_by, "created_at": created_at,
                "revoked_at": None,
            }
            return _Cursor()
        if "SELECT credential_id,tenant_id,name,credential_type,secret_ref,scopes" in query:
            return _Cursor(self.credentials.get((params[0], params[1])))
        if "INSERT INTO platform_credential_grants" in query:
            tenant_id, credential_id, consumer_type, consumer_id, scopes, granted_by, created_at = params[:7]
            if (tenant_id, credential_id) in self.credentials:
                self.grants[(tenant_id, credential_id, consumer_type, consumer_id)] = {
                    "tenant_id": tenant_id, "credential_id": credential_id,
                    "consumer_type": consumer_type, "consumer_id": consumer_id,
                    "scopes": scopes, "granted_by": granted_by,
                    "created_at": created_at,
                }
            return _Cursor()
        if "UPDATE platform_credentials SET revoked_at" in query:
            tenant_id, credential_id = params
            row = self.credentials.get((tenant_id, credential_id))
            if row is None or row["revoked_at"] is not None:
                return _Cursor()
            row["revoked_at"] = datetime.now(timezone.utc)
            return _Cursor({"credential_id": credential_id})
        if "DELETE FROM platform_credential_grants" in query:
            tenant_id, credential_id = params
            self.grants = {
                key: value for key, value in self.grants.items()
                if key[:2] != (tenant_id, credential_id)
            }
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {query}")


class _FailingAtomicAudit:
    async def record_on_connection(self, _connection, **_event):
        raise RuntimeError("audit database unavailable")


class _SecretBackend:
    def __init__(self):
        self.values = {}

    async def put(self, secret_ref, version, value):
        self.values[(secret_ref, version)] = value

    async def get(self, secret_ref, version):
        return self.values.get((secret_ref, version))

    async def revoke(self, secret_ref, version):
        self.values.pop((secret_ref, version), None)


class _SecretMetadata:
    def __init__(self):
        self.rows = {}

    async def list_versions(self, secret_ref):
        return [row for (ref, _), row in self.rows.items() if ref == secret_ref]

    async def create_version(self, secret_ref, version, created_at):
        self.rows[(secret_ref, version)] = SecretVersion(
            secret_ref, version, "pending", created_at,
        )

    async def activate_version(self, secret_ref, version):
        row = self.rows[(secret_ref, version)]
        self.rows[(secret_ref, version)] = SecretVersion(
            secret_ref, version, "active", row.created_at,
        )

    async def revoke_version(self, secret_ref, version, revoked_at):
        row = self.rows[(secret_ref, version)]
        self.rows[(secret_ref, version)] = SecretVersion(
            secret_ref, version, "revoked", row.created_at, revoked_at,
        )

    async def revoke_all(self, secret_ref, revoked_at):
        for key, row in list(self.rows.items()):
            if key[0] == secret_ref and row.status in {"active", "pending"}:
                self.rows[key] = SecretVersion(
                    secret_ref, row.version, "revoked", row.created_at, revoked_at,
                )
        return await self.list_pending_revocations(secret_ref)

    async def list_pending_revocations(self, secret_ref=None):
        return [
            row.version for (ref, _), row in self.rows.items()
            if ref == secret_ref and row.status in {"pending", "revoked"}
            and row.backend_revoked_at is None
        ]

    async def mark_backend_revoked(self, secret_ref, version, revoked_at):
        row = self.rows[(secret_ref, version)]
        self.rows[(secret_ref, version)] = SecretVersion(
            secret_ref, version, "revoked", row.created_at, revoked_at,
        )


class CredentialAuditAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = _TransactionalConnection()

        async def connect():
            return self.connection

        self.repository = PostgresCredentialRepository(connect)
        self.backend = _SecretBackend()
        self.secret_manager = SecretManager(
            backend=self.backend, metadata=_SecretMetadata(),
        )
        self.service = CredentialService(
            secrets=self.secret_manager,
            repository=self.repository,
            audit=_FailingAtomicAudit(),
        )
        self.now = datetime.now(timezone.utc)
        self.credential_id = "credential-a"
        self.secret_ref = "secret://tenant-a/credentials/credential-a"

    def _seed_credential(self):
        self.connection.credentials[("tenant-a", self.credential_id)] = {
            "credential_id": self.credential_id, "tenant_id": "tenant-a",
            "name": "Integration credential", "credential_type": "api_key",
            "secret_ref": self.secret_ref, "scopes": ["repo.read"],
            "created_by": "owner", "created_at": self.now, "revoked_at": None,
        }

    async def _seed_secret(self):
        await self.secret_manager.create(
            secret_ref=self.secret_ref, value="credential-secret",
        )

    async def test_create_rolls_back_metadata_and_revokes_unindexed_secret(self):
        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await self.service.create(
                tenant_id="tenant-a", name="New credential",
                credential_type="api_key", value="new-secret",
                scopes=("repo.read",), created_by="owner",
            )

        self.assertEqual(self.connection.credentials, {})
        self.assertEqual(self.backend.values, {})

    async def test_grant_rolls_back_access_when_audit_insert_fails(self):
        self._seed_credential()
        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await self.service.grant(
                tenant_id="tenant-a", credential_id=self.credential_id,
                consumer_type="agent", consumer_id="agent-a",
                scopes=("repo.read",), granted_by="owner",
            )

        self.assertEqual(self.connection.grants, {})

    async def test_revoke_rolls_back_metadata_and_grants_when_audit_fails(self):
        self._seed_credential()
        await self._seed_secret()
        grant_key = ("tenant-a", self.credential_id, "agent", "agent-a")
        self.connection.grants[grant_key] = {
            "tenant_id": "tenant-a", "credential_id": self.credential_id,
            "consumer_type": "agent", "consumer_id": "agent-a",
            "scopes": ["repo.read"], "granted_by": "owner",
            "created_at": self.now,
        }

        with self.assertRaisesRegex(RuntimeError, "audit database unavailable"):
            await self.service.revoke(
                tenant_id="tenant-a", credential_id=self.credential_id,
                actor_id="owner",
            )

        self.assertIsNone(
            self.connection.credentials[("tenant-a", self.credential_id)]["revoked_at"]
        )
        self.assertIn(grant_key, self.connection.grants)
        self.assertEqual(
            await self.secret_manager.resolve(secret_ref=self.secret_ref),
            "credential-secret",
        )


if __name__ == "__main__":
    unittest.main()
