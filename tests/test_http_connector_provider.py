from __future__ import annotations

import unittest

from agent_platform.control_plane.data_connectors import ConnectorError
from agent_platform.control_plane.http_connector_provider import (
    HTTPJSONConnectorProvider,
    JSONHTTPResponse,
)


class FakeHTTPClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.response


class HTTPJSONConnectorProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_fetch_page_maps_cursor_items_and_scopes(self):
        client = FakeHTTPClient(JSONHTTPResponse(
            200, b'{"items":[{"id":"doc-1","text":"hello"}],"next_cursor":{"page":2},"has_more":true}',
        ))
        provider = HTTPJSONConnectorProvider(
            list_endpoint="https://drive.example.test/v1/files",
            revoke_endpoint="https://drive.example.test/v1/revoke",
            allowed_hosts=frozenset({"drive.example.test"}), client=client,
        )
        page = await provider.fetch_page(
            credential="runtime-token", scopes=("files.read",),
            cursor={"page": 1},
        )
        self.assertEqual(page.items[0]["id"], "doc-1")
        self.assertEqual(page.next_cursor, {"page": 2})
        self.assertTrue(page.has_more)
        method, _, kwargs = client.calls[0]
        self.assertEqual(method, "GET")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer runtime-token")
        self.assertEqual(kwargs["params"]["scopes"], "files.read")

    async def test_provider_rejects_untrusted_or_non_https_endpoint(self):
        with self.assertRaises(ValueError):
            HTTPJSONConnectorProvider(
                list_endpoint="http://drive.example.test/files",
                revoke_endpoint="https://drive.example.test/revoke",
                allowed_hosts=frozenset({"drive.example.test"}),
            )
        with self.assertRaises(ValueError):
            HTTPJSONConnectorProvider(
                list_endpoint="https://evil.example.test/files",
                revoke_endpoint="https://drive.example.test/revoke",
                allowed_hosts=frozenset({"drive.example.test"}),
            )

    async def test_provider_rejects_nonadvancing_pagination(self):
        client = FakeHTTPClient(JSONHTTPResponse(
            200, b'{"items":[],"next_cursor":{"page":1},"has_more":true}',
        ))
        provider = HTTPJSONConnectorProvider(
            list_endpoint="https://drive.example.test/v1/files",
            revoke_endpoint="https://drive.example.test/v1/revoke",
            allowed_hosts=frozenset({"drive.example.test"}), client=client,
        )
        with self.assertRaises(ConnectorError):
            await provider.fetch_page(
                credential="token", scopes=("files.read",), cursor={"page": 1},
            )

    async def test_remote_revocation_uses_post_and_bearer_token(self):
        client = FakeHTTPClient(JSONHTTPResponse(204, b""))
        provider = HTTPJSONConnectorProvider(
            list_endpoint="https://drive.example.test/v1/files",
            revoke_endpoint="https://drive.example.test/v1/revoke",
            allowed_hosts=frozenset({"drive.example.test"}), client=client,
        )
        await provider.revoke(credential="runtime-token")
        self.assertEqual(client.calls[0][0], "POST")
        self.assertEqual(client.calls[0][2]["headers"]["Authorization"], "Bearer runtime-token")


if __name__ == "__main__":
    unittest.main()
