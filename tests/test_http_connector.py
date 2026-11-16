from __future__ import annotations

import json
import unittest

from agent_platform.control_plane.http_connector import (
    HTTPConnectorError, HTTPJSONConnectorProvider,
)


class Response:
    def __init__(self, payload, *, redirect=False, status_error=False):
        self.content = json.dumps(payload).encode()
        self._payload = payload
        self.is_redirect = redirect
        self.status_error = status_error
    def raise_for_status(self):
        if self.status_error:
            raise RuntimeError("HTTP failure")
    def json(self):
        return self._payload


class Client:
    def __init__(self, response):
        self.response = response
        self.calls = []
    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


class HTTPJSONConnectorProviderTests(unittest.IsolatedAsyncioTestCase):
    def test_requires_https_and_allowlisted_host(self):
        client = Client(Response({"items": []}))
        with self.assertRaises(ValueError):
            HTTPJSONConnectorProvider(
                client=client, base_url="http://source.example",
                allowed_hosts=frozenset({"source.example"}), list_path="/api/items",
            )
        with self.assertRaises(ValueError):
            HTTPJSONConnectorProvider(
                client=client, base_url="https://evil.example",
                allowed_hosts=frozenset({"source.example"}), list_path="/api/items",
            )

    async def test_fetch_page_sends_secret_and_cursor_without_redirects(self):
        client = Client(Response({
            "items": [{"id": "doc-1"}], "next_cursor": {"page": 2}, "has_more": True,
        }))
        provider = HTTPJSONConnectorProvider(
            client=client, base_url="https://source.example",
            allowed_hosts=frozenset({"source.example"}), list_path="/v1/items",
            revoke_path="/oauth/revoke",
        )
        page = await provider.fetch_page(
            credential="runtime-token", scopes=("files.read",),
            cursor={"page": 1},
        )
        self.assertEqual(page.items, ({"id": "doc-1"},))
        self.assertEqual(page.next_cursor, {"page": 2})
        method, url, kwargs = client.calls[0]
        self.assertEqual((method, url), ("GET", "https://source.example/v1/items"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer runtime-token")
        self.assertFalse(kwargs["follow_redirects"])
        self.assertEqual(kwargs["params"]["cursor"], '{"page":1}')

    async def test_redirect_and_malformed_pages_fail_closed(self):
        redirected = HTTPJSONConnectorProvider(
            client=Client(Response({}, redirect=True)),
            base_url="https://source.example",
            allowed_hosts=frozenset({"source.example"}), list_path="/v1/items",
        )
        with self.assertRaises(HTTPConnectorError):
            await redirected.fetch_page(credential="token", scopes=(), cursor=None)
        malformed = HTTPJSONConnectorProvider(
            client=Client(Response({"items": [], "has_more": True})),
            base_url="https://source.example",
            allowed_hosts=frozenset({"source.example"}), list_path="/v1/items",
        )
        with self.assertRaises(HTTPConnectorError):
            await malformed.fetch_page(credential="token", scopes=(), cursor=None)

    async def test_revoke_uses_fixed_endpoint(self):
        client = Client(Response({"revoked": True}))
        provider = HTTPJSONConnectorProvider(
            client=client, base_url="https://source.example",
            allowed_hosts=frozenset({"source.example"}), list_path="/v1/items",
            revoke_path="/oauth/revoke",
        )
        await provider.revoke(credential="runtime-token")
        self.assertEqual(client.calls[0][0:2], ("POST", "https://source.example/oauth/revoke"))


if __name__ == "__main__":
    unittest.main()
