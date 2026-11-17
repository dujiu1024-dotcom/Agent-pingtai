"""Trusted publisher registry and management API regression tests."""
import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.extension_publishers import create_extension_publisher_router
from agent_platform.extensions.oci_runtime import TrustedPublisherKey
from agent_platform.extensions.publishers import TrustedPublisherKeyRegistry
from agent_platform.extensions.postgres import (
    PostgresExtensionStateStore, PostgresTrustedPublisherKeyRepository,
    _manifest_dict, _manifest_from,
)
from agent_platform.extensions.manifest import ExtensionManifest
from agent_platform.identity.models import Principal


_PEM = b"-----BEGIN PUBLIC KEY-----\\nMCowBQYDK2VwAyEA\\n-----END PUBLIC KEY-----"


class _Repository:
    def __init__(self):
        self.items = {}
        self.rotations = []
        self.revocations = []

    async def register(self, **values):
        key = TrustedPublisherKey(
            publisher_id=values["publisher_id"], key_id=values["key_id"],
            public_key_pem=values["public_key_pem"],
            extension_ids=frozenset({values["extension_id"]}),
            tenant_ids=frozenset({values["tenant_id"]}),
            not_before=float(values["not_before"]),
            expires_at=values["expires_at"], revoked=False,
        )
        identity = (values["tenant_id"], values["extension_id"], values["key_id"])
        if identity in self.items:
            raise ValueError("duplicate key")
        self.items[identity] = key
        return key

    async def rotate(self, *, revoke_key_ids=(), **values):
        key = await self.register(**values)
        self.rotations.append((values["tenant_id"], values["extension_id"], tuple(revoke_key_ids)))
        for old_id in revoke_key_ids:
            identity = (values["tenant_id"], values["extension_id"], old_id)
            old = self.items.get(identity)
            if old:
                self.items[identity] = TrustedPublisherKey(
                    publisher_id=old.publisher_id, key_id=old.key_id,
                    public_key_pem=old.public_key_pem,
                    extension_ids=old.extension_ids, tenant_ids=old.tenant_ids,
                    not_before=old.not_before, expires_at=old.expires_at, revoked=True,
                )
        return key

    async def revoke(self, *, tenant_id, extension_id, key_id, revoked_by):
        identity = (tenant_id, extension_id, key_id)
        old = self.items.get(identity)
        self.revocations.append((identity, revoked_by))
        if old is None:
            return False
        self.items[identity] = TrustedPublisherKey(
            publisher_id=old.publisher_id, key_id=old.key_id,
            public_key_pem=old.public_key_pem, extension_ids=old.extension_ids,
            tenant_ids=old.tenant_ids, not_before=old.not_before,
            expires_at=old.expires_at, revoked=True,
        )
        return True

    async def resolve(self, tenant_id, extension_id, key_id):
        return self.items.get((tenant_id, extension_id, key_id))


class _Audit:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    async def record(self, **event):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


class TrustedPublisherRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = _Repository()
        self.registry = TrustedPublisherKeyRegistry(self.repository)
        self.values = {
            "tenant_id": "tenant-a", "extension_id": "sample-extension",
            "publisher_id": "publisher-a", "key_id": "key-1",
            "public_key_pem": _PEM, "not_before": 100.0,
            "expires_at": 500.0, "created_by": "admin-a",
        }

    async def test_register_and_resolve_is_tenant_and_extension_scoped(self):
        await self.registry.register(**self.values)
        resolved = await self.registry.resolve("tenant-a", "sample-extension", "key-1")
        self.assertEqual(resolved.publisher_id, "publisher-a")
        self.assertEqual(resolved.tenant_ids, frozenset({"tenant-a"}))
        self.assertIsNone(await self.registry.resolve("tenant-b", "sample-extension", "key-1"))
        self.assertIsNone(await self.registry.resolve("tenant-a", "other-extension", "key-1"))

    async def test_rotation_registers_new_key_and_revokes_old_key(self):
        await self.registry.register(**self.values)
        replacement = dict(self.values, key_id="key-2", public_key_pem=_PEM)
        await self.registry.rotate(**replacement, revoke_key_ids=("key-1",))
        self.assertTrue((await self.registry.resolve("tenant-a", "sample-extension", "key-1")).revoked)
        self.assertFalse((await self.registry.resolve("tenant-a", "sample-extension", "key-2")).revoked)
        self.assertEqual(self.repository.rotations[0][2], ("key-1",))

    async def test_invalid_private_key_material_is_rejected(self):
        values = dict(self.values, public_key_pem=b"-----BEGIN PRIVATE KEY-----secret")
        with self.assertRaises(ValueError):
            await self.registry.register(**values)
        self.assertEqual(self.repository.items, {})

    async def test_invalid_key_identifier_and_expiry_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.registry.register(**dict(self.values, key_id="../bad"))
        with self.assertRaises(ValueError):
            await self.registry.register(**dict(self.values, expires_at=99.0))
        with self.assertRaises(ValueError):
            await self.registry.register(**dict(self.values, not_before=float('nan')))
        self.assertEqual(self.repository.items, {})

    async def test_revoke_is_idempotent_and_visible_to_runtime_resolver(self):
        await self.registry.register(**self.values)
        self.assertTrue(await self.registry.revoke(
            tenant_id="tenant-a", extension_id="sample-extension",
            key_id="key-1", revoked_by="admin-a",
        ))
        self.assertTrue((await self.registry.resolve("tenant-a", "sample-extension", "key-1")).revoked)
        self.assertFalse(await self.registry.revoke(
            tenant_id="tenant-a", extension_id="sample-extension",
            key_id="missing", revoked_by="admin-a",
        ))


class ExtensionPublisherApiTests(unittest.TestCase):
    def setUp(self):
        self.repository = _Repository()
        self.registry = TrustedPublisherKeyRegistry(self.repository)
        self.audit = _Audit()
        self.actor = Principal(
            user_id="admin-a", tenant_id="tenant-a", session_id="session-a",
            email="admin@example.com", display_name="Admin",
        )
        app = FastAPI()

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.identity = type("IdentityState", (), {"principal": self.actor})()
            return await call_next(request)

        app.include_router(create_extension_publisher_router(
            registry=self.registry,
            authorize_manage=lambda actor, extension_id, action: actor.tenant_id == "tenant-a",
            audit_writer=self.audit,
        ))
        self.client = TestClient(app)

    def _payload(self, **overrides):
        body = {
            "extension_id": "sample-extension",
            "publisher_id": "publisher-a",
            "key_id": "key-1",
            "public_key_pem": _PEM.decode("ascii"),
            "not_before": 100,
            "expires_at": 500,
        }
        body.update(overrides)
        return body

    def test_register_uses_authenticated_tenant_and_actor_and_audits(self):
        response = self.client.post(
            "/api/platform/v1/extensions/publishers/keys", json=self._payload(),
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["tenant_id"], "tenant-a")
        self.assertEqual(self.audit.events[0]["outcome"], "requested")
        self.assertEqual(self.audit.events[1]["outcome"], "succeeded")
        self.assertEqual(self.repository.items[("tenant-a", "sample-extension", "key-1")].publisher_id,
                         "publisher-a")

    def test_rotation_revokes_previous_key(self):
        self.client.post("/api/platform/v1/extensions/publishers/keys", json=self._payload())
        response = self.client.post(
            "/api/platform/v1/extensions/publishers/keys",
            json=self._payload(key_id="key-2", revoke_key_ids=["key-1"]),
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(self.repository.items[("tenant-a", "sample-extension", "key-1")].revoked)

    def test_audit_failure_prevents_registry_side_effect(self):
        self.audit.fail = True
        response = self.client.post(
            "/api/platform/v1/extensions/publishers/keys", json=self._payload(),
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.repository.items, {})

    def test_invalid_pem_is_rejected_before_persistence(self):
        response = self.client.post(
            "/api/platform/v1/extensions/publishers/keys",
            json=self._payload(public_key_pem="-----BEGIN PRIVATE KEY-----secret"),
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.repository.items, {})

    def test_revoke_is_tenant_bound_and_audited(self):
        self.client.post("/api/platform/v1/extensions/publishers/keys", json=self._payload())
        response = self.client.post(
            "/api/platform/v1/extensions/publishers/sample-extension/keys/key-1/revoke",
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(self.repository.items[("tenant-a", "sample-extension", "key-1")].revoked)



class ExtensionPostgresMappingTests(unittest.TestCase):
    def test_persisted_manifest_round_trips_dependency_pins_and_permissions(self):
        manifest = ExtensionManifest(
            extension_id="sample-extension", version="1.2.3", platform_api="1.0.0",
            permissions=("knowledge.read",), dependencies=("base-extension",),
            entrypoint="plugin.py", dependency_versions={"base-extension": "2.0.0"},
        )
        restored = _manifest_from(_manifest_dict(manifest))
        self.assertEqual(restored, manifest)

    def test_production_state_store_rejects_non_reference_artifacts(self):
        store = PostgresExtensionStateStore(
            connection_factory=lambda: None, tenant_id="tenant-a",
        )
        item = type("Item", (), {
            "manifest": ExtensionManifest(
                extension_id="sample-extension", version="1.0.0",
                platform_api="1.0.0", permissions=(), entrypoint="plugin.py",
            ),
            "status": "disabled", "artifact": object(),
        })()
        with self.assertRaisesRegex(ValueError, "immutable artifact reference"):
            import asyncio
            asyncio.run(store.put(item))

    def test_postgres_key_row_maps_tenant_and_extension_binding(self):
        row = {
            "tenant_id": "tenant-a", "extension_id": "sample-extension",
            "publisher_id": "publisher-a", "key_id": "key-1",
            "public_key_pem": _PEM,
            "not_before": datetime.fromtimestamp(100, timezone.utc),
            "expires_at": datetime.fromtimestamp(500, timezone.utc),
            "revoked_at": None,
        }
        key = PostgresTrustedPublisherKeyRepository._to_key(row)
        self.assertEqual(key.tenant_ids, frozenset({"tenant-a"}))
        self.assertEqual(key.extension_ids, frozenset({"sample-extension"}))
        self.assertFalse(key.revoked)
        self.assertEqual(key.expires_at, 500.0)


if __name__ == "__main__":
    unittest.main()
