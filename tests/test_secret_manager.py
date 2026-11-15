from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.security.secrets import SecretManager, SecretVersion


class FakeMetadata:
    def __init__(self):
        self.rows = {}

    async def list_versions(self, secret_ref):
        return [self.rows[k] for k in sorted(self.rows) if k[0] == secret_ref]

    async def create_version(self, secret_ref, version, created_at):
        key = (secret_ref, version)
        if key in self.rows:
            raise ValueError("duplicate version")
        self.rows[key] = SecretVersion(secret_ref, version, "pending", created_at)

    async def activate_version(self, secret_ref, version):
        for key, row in list(self.rows.items()):
            if key[0] == secret_ref and row.status == "active":
                self.rows[key] = SecretVersion(secret_ref, row.version, "revoked", row.created_at)
        key = (secret_ref, version)
        row = self.rows[key]
        self.rows[key] = SecretVersion(secret_ref, version, "active", row.created_at)

    async def revoke_all(self, secret_ref, revoked_at):
        versions = []
        for key, row in list(self.rows.items()):
            if key[0] == secret_ref and row.status in {"active", "pending"}:
                self.rows[key] = SecretVersion(secret_ref, row.version, "revoked", row.created_at)
                versions.append(row.version)
        return versions


class FakeVault:
    def __init__(self):
        self.values = {}

    async def put(self, secret_ref, version, value):
        self.values[(secret_ref, version)] = value

    async def get(self, secret_ref, version):
        return self.values.get((secret_ref, version))

    async def revoke(self, secret_ref, version):
        self.values.pop((secret_ref, version), None)


class SecretManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.vault = FakeVault()
        self.metadata = FakeMetadata()
        self.manager = SecretManager(backend=self.vault, metadata=self.metadata)
        self.ref = "secret://tenant-a/webhooks/provider"

    async def test_create_resolve_and_metadata_never_contains_secret(self):
        created = await self.manager.create(secret_ref=self.ref, value="initial-secret")
        self.assertEqual(created.version, 1)
        self.assertEqual(await self.manager.resolve(secret_ref=self.ref), "initial-secret")
        described = await self.manager.describe(secret_ref=self.ref)
        self.assertEqual(described[0].status, "active")
        self.assertNotIn("initial-secret", repr(described))

    async def test_rotate_activates_new_version_and_revokes_old_backend_value(self):
        await self.manager.create(secret_ref=self.ref, value="old-secret")
        rotated = await self.manager.rotate(secret_ref=self.ref, value="new-secret")
        self.assertEqual(rotated.version, 2)
        self.assertEqual(await self.manager.resolve(secret_ref=self.ref), "new-secret")
        self.assertNotIn((self.ref, 1), self.vault.values)

    async def test_revoke_removes_all_versions_and_fails_closed(self):
        await self.manager.create(secret_ref=self.ref, value="old-secret")
        await self.manager.rotate(secret_ref=self.ref, value="new-secret")
        self.assertEqual(await self.manager.revoke(secret_ref=self.ref), 2)
        with self.assertRaises(KeyError):
            await self.manager.resolve(secret_ref=self.ref)

    async def test_invalid_refs_and_duplicate_create_are_rejected(self):
        with self.assertRaises(ValueError):
            await self.manager.create(secret_ref="secret://tenant-a/../escape", value="secret")
        await self.manager.create(secret_ref=self.ref, value="initial-secret")
        with self.assertRaises(ValueError):
            await self.manager.create(secret_ref=self.ref, value="replacement")


if __name__ == "__main__":
    unittest.main()
