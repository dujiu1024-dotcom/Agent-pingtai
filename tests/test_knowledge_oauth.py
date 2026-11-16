from __future__ import annotations

import json
import unittest

from agent_platform.knowledge.oauth import OAuthCredentialError, OAuthRefreshTokenResolver


class SecretManager:
    def __init__(self, values):
        self.values = dict(values)
        self.rotations = []

    async def resolve(self, *, secret_ref):
        return self.values[secret_ref]

    async def rotate(self, *, secret_ref, value):
        self.rotations.append((secret_ref, value))
        self.values[secret_ref] = value


class Response:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class OAuthRefreshTokenResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_rotates_secret_and_caches_access_token_per_reference(self):
        refs = {
            "secret://tenant-a/google": json.dumps({
                "client_id": "google-client-a",
                "client_secret": "client-secret-a",
                "refresh_token": "refresh-a",
            }),
            "secret://tenant-b/google": json.dumps({
                "client_id": "google-client-b",
                "client_secret": "client-secret-b",
                "refresh_token": "refresh-b",
            }),
        }
        secrets = SecretManager(refs)
        client = Client([
            Response({"access_token": "access-a", "expires_in": 3600, "refresh_token": "refresh-a-rotated"}),
            Response({"access_token": "access-b", "expires_in": 3600}),
        ])
        resolver = OAuthRefreshTokenResolver(
            client=client, secret_manager=secrets,
            token_url="https://oauth2.googleapis.com/token",
        )
        self.assertEqual(await resolver.resolve("secret://tenant-a/google"), "access-a")
        self.assertEqual(await resolver.resolve("secret://tenant-a/google"), "access-a")
        self.assertEqual(await resolver.resolve("secret://tenant-b/google"), "access-b")
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(secrets.rotations), 1)
        self.assertEqual(
            json.loads(secrets.rotations[0][1])["refresh_token"],
            "refresh-a-rotated",
        )
        self.assertEqual(
            client.calls[0][1]["data"]["grant_type"], "refresh_token",
        )

    async def test_rejects_unapproved_token_endpoint(self):
        with self.assertRaises(ValueError):
            OAuthRefreshTokenResolver(
                client=Client([]), secret_manager=SecretManager({}),
                token_url="https://attacker.example/token",
            )

    async def test_rejects_redirect_from_oauth_endpoint(self):
        resolver = OAuthRefreshTokenResolver(
            client=Client([Response({}, status_code=302)]),
            secret_manager=SecretManager({
                "secret://tenant/google": json.dumps({
                    "client_id": "client", "client_secret": "secret",
                    "refresh_token": "refresh",
                }),
            }),
            token_url="https://oauth2.googleapis.com/token",
        )
        with self.assertRaises(OAuthCredentialError):
            await resolver.resolve("secret://tenant/google")

    async def test_rejects_malformed_oauth_response(self):
        resolver = OAuthRefreshTokenResolver(
            client=Client([Response({"access_token": "access", "expires_in": 0})]),
            secret_manager=SecretManager({
                "secret://tenant/google": json.dumps({
                    "client_id": "client", "client_secret": "secret",
                    "refresh_token": "refresh",
                }),
            }),
            token_url="https://oauth2.googleapis.com/token",
        )
        with self.assertRaises(OAuthCredentialError):
            await resolver.resolve("secret://tenant/google")


if __name__ == "__main__":
    unittest.main()
