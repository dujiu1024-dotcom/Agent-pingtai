"""PostgreSQL persistence contract tests for extension publisher trust."""
import unittest
from datetime import datetime, timezone

from agent_platform.extensions.postgres import (
    PostgresExtensionStateStore,
    PostgresTrustedPublisherKeyRepository,
)


_PUBLIC_KEY = b"-----BEGIN PUBLIC KEY-----\\nMCowBQYDK2VwAyEA\\n-----END PUBLIC KEY-----"


class _Cursor:
    def __init__(self, *, row=None, rows=None, rowcount=0):
        self.row = row
        self.rows = list(rows or [])
        self.rowcount = rowcount

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _Transaction:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        self.connection.transaction_started = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.connection.transaction_rolled_back = exc_type is not None
        self.connection.transaction_committed = exc_type is None
        return False


class _Connection:
    def __init__(self, *, key_row=None, extension_row=None, revoke_rowcount=0):
        self.key_row = key_row
        self.extension_row = extension_row
        self.revoke_rowcount = revoke_rowcount
        self.queries = []
        self.transaction_started = False
        self.transaction_committed = False
        self.transaction_rolled_back = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return _Transaction(self)

    async def execute(self, query, params=()):
        normalized = " ".join(query.split())
        self.queries.append((normalized, params))
        if "INSERT INTO platform_trusted_publisher_keys" in normalized:
            return _Cursor(row=self.key_row, rowcount=1 if self.key_row else 0)
        if "key_id=ANY(%s)" in normalized:
            return _Cursor(rowcount=self.revoke_rowcount)
        if "UPDATE platform_trusted_publisher_keys" in normalized and "RETURNING key_id" in normalized:
            return _Cursor(row={"key_id": "key-1"} if self.key_row else None, rowcount=1 if self.key_row else 0)
        if "SELECT tenant_id,extension_id,publisher_id,key_id,public_key_pem" in normalized:
            return _Cursor(row=self.key_row)
        if "SELECT manifest,status,artifact_ref" in normalized:
            return _Cursor(row=self.extension_row)
        if "SELECT manifest,status,artifact_ref" in normalized and "ORDER BY extension_id" in normalized:
            return _Cursor(rows=[self.extension_row] if self.extension_row else [])
        return _Cursor()


def _key_row(*, tenant_id="tenant-a", extension_id="sample-extension",
             key_id="key-1", revoked_at=None):
    return {
        "tenant_id": tenant_id,
        "extension_id": extension_id,
        "publisher_id": "publisher-a",
        "key_id": key_id,
        "public_key_pem": _PUBLIC_KEY,
        "not_before": datetime.fromtimestamp(100, timezone.utc),
        "expires_at": datetime.fromtimestamp(500, timezone.utc),
        "revoked_at": revoked_at,
    }


def _manifest():
    return {
        "extension_id": "sample-extension",
        "version": "1.0.0",
        "platform_api": "1.0.0",
        "permissions": ["knowledge.read"],
        "dependencies": [],
        "entrypoint": "plugin.py",
        "dependency_versions": {},
    }


class PostgresTrustedPublisherRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_uses_tenant_extension_key_unique_scope(self):
        connection = _Connection(key_row=_key_row())

        async def connect():
            return connection

        repository = PostgresTrustedPublisherKeyRepository(connect)
        key = await repository.register(
            tenant_id="tenant-a", extension_id="sample-extension",
            publisher_id="publisher-a", key_id="key-1",
            public_key_pem=_PUBLIC_KEY, not_before=100,
            expires_at=500, created_by="admin-a",
        )
        query, params = connection.queries[0]
        self.assertIn("ON CONFLICT (tenant_id,extension_id,key_id) DO NOTHING", query)
        self.assertEqual(params[:4], ("tenant-a", "sample-extension", "publisher-a", "key-1"))
        self.assertEqual(key.tenant_ids, frozenset({"tenant-a"}))
        self.assertEqual(key.extension_ids, frozenset({"sample-extension"}))

    async def test_rotation_is_transactional_and_revokes_only_requested_scope(self):
        connection = _Connection(key_row=_key_row(), revoke_rowcount=2)

        async def connect():
            return connection

        repository = PostgresTrustedPublisherKeyRepository(connect)
        await repository.rotate(
            tenant_id="tenant-a", extension_id="sample-extension",
            publisher_id="publisher-a", key_id="key-2",
            public_key_pem=_PUBLIC_KEY, not_before=100,
            expires_at=500, created_by="admin-a",
            revoke_key_ids=("key-old-a", "key-old-b"),
        )
        self.assertTrue(connection.transaction_started)
        self.assertTrue(connection.transaction_committed)
        update_query, update_params = connection.queries[1]
        self.assertIn("WHERE tenant_id=%s AND extension_id=%s", update_query)
        self.assertEqual(update_params, ("admin-a", "tenant-a", "sample-extension", ["key-old-a", "key-old-b"]))

    async def test_rotation_rolls_back_when_any_previous_key_is_missing(self):
        connection = _Connection(key_row=_key_row(key_id="key-new"), revoke_rowcount=1)

        async def connect():
            return connection

        repository = PostgresTrustedPublisherKeyRepository(connect)
        with self.assertRaisesRegex(ValueError, "not found"):
            await repository.rotate(
                tenant_id="tenant-a", extension_id="sample-extension",
                publisher_id="publisher-a", key_id="key-new",
                public_key_pem=_PUBLIC_KEY, not_before=100,
                expires_at=500, created_by="admin-a",
                revoke_key_ids=("key-old-a", "key-old-b"),
            )
        self.assertTrue(connection.transaction_rolled_back)
        self.assertFalse(connection.transaction_committed)

    async def test_revoke_records_actor_and_scopes_tenant_extension_key(self):
        connection = _Connection(key_row=_key_row())

        async def connect():
            return connection

        repository = PostgresTrustedPublisherKeyRepository(connect)
        self.assertTrue(await repository.revoke(
            tenant_id="tenant-a", extension_id="sample-extension",
            key_id="key-1", revoked_by="admin-a",
        ))
        query, params = connection.queries[0]
        self.assertIn("revoked_by=%s", query)
        self.assertIn("tenant_id=%s AND extension_id=%s AND key_id=%s", query)
        self.assertEqual(params, ("admin-a", "tenant-a", "sample-extension", "key-1"))

    async def test_resolve_uses_exact_tenant_extension_and_key(self):
        connection = _Connection(key_row=_key_row(revoked_at=datetime.now(timezone.utc)))

        async def connect():
            return connection

        repository = PostgresTrustedPublisherKeyRepository(connect)
        key = await repository.resolve("tenant-a", "sample-extension", "key-1")
        self.assertTrue(key.revoked)
        query, params = connection.queries[0]
        self.assertIn("WHERE tenant_id=%s AND extension_id=%s AND key_id=%s", query)
        self.assertEqual(params, ("tenant-a", "sample-extension", "key-1"))


class PostgresExtensionStateStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_is_tenant_scoped_and_reconstructs_manifest_and_artifact_ref(self):
        connection = _Connection(extension_row={
            "manifest": _manifest(),
            "status": "enabled",
            "artifact_ref": "sha256:" + "a" * 64,
        })

        async def connect():
            return connection

        store = PostgresExtensionStateStore(connection_factory=connect, tenant_id="tenant-a")
        item = await store.get("sample-extension")
        self.assertEqual(item.manifest.extension_id, "sample-extension")
        self.assertEqual(item.status, "enabled")
        self.assertEqual(item.artifact, "sha256:" + "a" * 64)
        query, params = connection.queries[0]
        self.assertIn("WHERE tenant_id=%s AND extension_id=%s", query)
        self.assertEqual(params, ("tenant-a", "sample-extension"))

    async def test_put_persists_tenant_manifest_and_immutable_artifact_reference(self):
        connection = _Connection()

        async def connect():
            return connection

        store = PostgresExtensionStateStore(connection_factory=connect, tenant_id="tenant-a")
        from agent_platform.extensions.manager import InstalledExtension
        from agent_platform.extensions.manifest import ExtensionManifest

        manifest = ExtensionManifest(
            extension_id="sample-extension", version="1.0.0",
            platform_api="1.0.0", permissions=("knowledge.read",),
            entrypoint="plugin.py",
        )
        await store.put(InstalledExtension(
            manifest=manifest, status="disabled",
            artifact="sha256:" + "b" * 64,
        ))
        query, params = connection.queries[0]
        self.assertIn("ON CONFLICT (tenant_id,extension_id) DO UPDATE", query)
        self.assertEqual(params[0:2], ("tenant-a", "sample-extension"))
        self.assertIn('"extension_id":"sample-extension"', params[2])
        self.assertEqual(params[4], "sha256:" + "b" * 64)


if __name__ == "__main__":
    unittest.main()
