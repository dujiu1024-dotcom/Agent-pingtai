from __future__ import annotations

import json
import unittest

from agent_platform.security.kms_envelope import KmsEnvelopeSecretBackend
from agent_platform.security.vault_auth import VaultAppRoleTokenProvider


class FakeKms:
    def __init__(self):
        self.keys = {}
        self.contexts = []

    async def generate_data_key(self, *, key_id, encryption_context):
        import os
        key = os.urandom(32)
        wrapped = b"wrapped:" + key
        self.keys[wrapped] = key
        self.contexts.append(dict(encryption_context))
        return key, wrapped

    async def decrypt_data_key(self, *, ciphertext_blob, encryption_context):
        self.contexts.append(dict(encryption_context))
        return self.keys[ciphertext_blob]


class FakeStore:
    def __init__(self):
        self.rows = {}

    async def put(self, secret_ref, version, envelope):
        self.rows[(secret_ref, version)] = envelope

    async def get(self, secret_ref, version):
        return self.rows.get((secret_ref, version))

    async def delete(self, secret_ref, version):
        self.rows.pop((secret_ref, version), None)


class KmsEnvelopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trip_context_binding_and_revoke(self):
        kms, store = FakeKms(), FakeStore()
        backend = KmsEnvelopeSecretBackend(kms=kms, store=store, key_id="arn:aws:kms:region:acct:key/id")
        ref = "secret://tenant-a/provider/api"
        await backend.put(ref, 4, "do-not-log-this")
        self.assertEqual(await backend.get(ref, 4), "do-not-log-this")
        self.assertEqual(kms.contexts[0], kms.contexts[1])
        self.assertEqual(kms.contexts[0]["secret_ref"], ref)
        await backend.revoke(ref, 4)
        self.assertIsNone(await backend.get(ref, 4))

    async def test_envelope_tampering_fails_closed(self):
        backend = KmsEnvelopeSecretBackend(kms=FakeKms(), store=FakeStore(), key_id="key-id")
        await backend.put("secret://tenant-a/path", 1, "secret")
        store = backend._store
        store.rows[("secret://tenant-a/path", 1)] = b"tampered"
        with self.assertRaisesRegex(RuntimeError, "envelope"):
            await backend.get("secret://tenant-a/path", 1)


class FakeVaultAuthHttp:
    def __init__(self):
        self.calls = []
        self.token_number = 0

    def request(self, method, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body)))
        if url.endswith("/auth/approle/login"):
            self.token_number += 1
            return 200, json.dumps({"auth":{"client_token":f"token-{self.token_number}","lease_duration":3600}}).encode()
        if url.endswith("/auth/token/renew-self"):
            return 200, json.dumps({"auth":{"client_token":"renewed-token","lease_duration":3600}}).encode()
        return 403, b"secret response must not escape"


class VaultAppRoleTests(unittest.IsolatedAsyncioTestCase):
    async def test_approle_login_is_cached_and_secret_id_is_not_logged(self):
        http = FakeVaultAuthHttp()
        provider = VaultAppRoleTokenProvider(
            address="https://vault.example", role_id="role-id", secret_id="secret-id",
            request_func=http.request,
        )
        self.assertEqual(await provider(), "token-1")
        self.assertEqual(await provider(), "token-1")
        self.assertEqual(len(http.calls), 1)
        self.assertEqual(http.calls[0][2]["secret_id"], "secret-id")

    async def test_renews_cached_token_before_reauthentication(self):
        now = [0.0]
        http = FakeVaultAuthHttp()
        provider = VaultAppRoleTokenProvider(
            address="https://vault.example", role_id="role-id", secret_id="secret-id",
            request_func=http.request, clock=lambda: now[0],
        )
        self.assertEqual(await provider(), "token-1")
        now[0] = 2600
        self.assertEqual(await provider(), "renewed-token")
        self.assertTrue(http.calls[-1][0].endswith("/auth/token/renew-self"))
