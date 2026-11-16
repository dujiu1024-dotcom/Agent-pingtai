from __future__ import annotations

import unittest

from agent_platform.security.secrets import SecretManager, SecretVersion
from agent_platform.security.vault_kv_v2 import VaultKvV2SecretBackend


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

    async def revoke_version(self, secret_ref, version, revoked_at):
        key = (secret_ref, version)
        row = self.rows.get(key)
        if row and row.status == "pending":
            self.rows[key] = SecretVersion(secret_ref, version, "revoked", row.created_at)

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
        self.assertEqual(await self.manager.revoke(secret_ref=self.ref), 1)
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



class FakeVaultHttp:
    def __init__(self):
        self.calls = []
        self.values = {}

    def request(self, method, url, headers, body, timeout):
        import json
        self.calls.append((method, url, headers, body, timeout))
        if "/data/" in url and method == "POST":
            self.values[url] = json.loads(body)["data"]["value"]
            return 200, b'{"data":{"version":1}}'
        if "/data/" in url and method == "GET":
            if url not in self.values:
                return 404, b"{}"
            return 200, json.dumps({"data":{"data":{"value":self.values[url]}}}).encode()
        if "/destroy/" in url and method == "POST":
            self.values.pop(url.replace("/destroy/", "/data/"), None)
            return 204, b""
        if "/metadata/" in url and method == "DELETE":
            return 204, b""
        return 400, b"{}"


class VaultKvV2SecretBackendTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_and_versioned_destroy(self):
        http = FakeVaultHttp()
        backend = VaultKvV2SecretBackend(
            address="https://vault.example",
            token="vault-test-token",
            mount="platform-secrets",
            namespace="team/platform",
            request_func=http.request,
        )
        ref = "secret://tenant-a/webhooks/provider"
        await backend.put(ref, 3, "secret-value")
        self.assertEqual(await backend.get(ref, 3), "secret-value")
        await backend.revoke(ref, 3)
        self.assertIsNone(await backend.get(ref, 3))
        self.assertIn("/v1/platform-secrets/data/tenant-a/webhooks/provider/versions/3", http.calls[0][1])
        self.assertIn("/v1/platform-secrets/destroy/tenant-a/webhooks/provider/versions/3", http.calls[2][1])
        self.assertEqual(http.calls[0][2]["X-Vault-Token"], "vault-test-token")
        self.assertEqual(http.calls[0][2]["X-Vault-Namespace"], "team/platform")

    async def test_rejects_insecure_address_and_invalid_version(self):
        with self.assertRaises(ValueError):
            VaultKvV2SecretBackend(address="http://vault.example", token="token")
        backend = VaultKvV2SecretBackend(
            address="https://vault.example", token="token", request_func=FakeVaultHttp().request
        )
        with self.assertRaises(ValueError):
            await backend.get("secret://tenant-a/path", 0)

    async def test_vault_errors_do_not_echo_response_body_or_secret(self):
        def failing_request(method, url, headers, body, timeout):
            return 403, b'{"errors":["token secret-value denied"]}'
        backend = VaultKvV2SecretBackend(
            address="https://vault.example", token="vault-token", request_func=failing_request
        )
        with self.assertRaisesRegex(RuntimeError, "HTTP 403") as raised:
            await backend.put("secret://tenant-a/path", 1, "secret-value")
        self.assertNotIn("secret-value", str(raised.exception))
        self.assertNotIn("vault-token", str(raised.exception))
