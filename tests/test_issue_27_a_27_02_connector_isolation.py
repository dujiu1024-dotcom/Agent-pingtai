from __future__ import annotations

import asyncio
import unittest

from agent_platform.connectors.base import SecretStoreConnectorAuthenticator
from agent_platform.control_plane.http_connector_provider import (
    HTTPJSONConnectorProvider,
    JSONHTTPResponse,
)
from agent_platform.knowledge.google_drive_connector import GoogleDriveKnowledgeConnector
from agent_platform.knowledge.sharepoint_connector import SharePointKnowledgeConnector
from agent_platform.tools.connector_catalog import (
    ConnectorCatalog,
    InMemoryConnectorManifestRegistry,
)


class FakeSecretStore:
    def __init__(self):
        self.calls = []

    async def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return f"token-for-{kwargs['credential_id']}"


class FakeHTTPClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return JSONHTTPResponse(200, b'{"items":[],"has_more":false}')


class NewCatalogConnector:
    def __init__(self):
        self.started = 0
        self.closed = 0

    async def start(self):
        self.started += 1

    async def close(self):
        self.closed += 1


def connector_manifest():
    return {
        "connector_id": "new-calendar",
        "name": "Calendar",
        "version": "1.0.0",
        "description": "New calendar connector used by isolation regression test",
        "auth_type": "oauth",
        "operations": [{
            "name": "list-events",
            "description": "List calendar events",
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }],
    }


class Issue27A2702ConnectorIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_register_load_and_unload_new_connector_preserves_existing_connectors(self):
        secret_store = FakeSecretStore()
        authenticator = SecretStoreConnectorAuthenticator(
            secret_store=secret_store,
            consumer_type="agent",
            consumer_id="agent-17",
            actor_id="system:connector-worker",
        )
        google = GoogleDriveKnowledgeConnector(
            client=object(),
            credential_ref="secret://tenant-a/credentials/drive",
            document_factory=lambda *args: None,
            authenticator=authenticator,
            auth_scopes=("drive.readonly",),
        )
        sharepoint = SharePointKnowledgeConnector(
            client=object(),
            credential_ref="secret://tenant-a/credentials/sharepoint",
            drive_id="drive-1",
            document_factory=lambda *args: None,
            credential_revoker=lambda _ref: None,
            authenticator=authenticator,
            auth_scopes=("sites.read.all",),
        )
        http_client = FakeHTTPClient()
        http = HTTPJSONConnectorProvider(
            list_endpoint="https://connector.example.test/files",
            revoke_endpoint="https://connector.example.test/revoke",
            allowed_hosts=frozenset({"connector.example.test"}),
            client=http_client,
            credential_ref="secret://tenant-a/credentials/http",
            authenticator=authenticator,
            auth_scopes=("files.read",),
        )

        created = []

        def factory(_manifest):
            connector = NewCatalogConnector()
            created.append(connector)
            return connector

        catalog = ConnectorCatalog(
            registry=InMemoryConnectorManifestRegistry(),
            connector_factory=factory,
        )

        await catalog.register(tenant_id="tenant-a", manifest=connector_manifest())
        self.assertEqual(created, [])
        self.assertEqual(await catalog.loaded_ids(tenant_id="tenant-a"), ())

        # Load the new connector while the existing Google Drive, SharePoint and
        # HTTP providers authenticate and execute their established request path.
        new_connector, google_token, sharepoint_token, _ = await asyncio.gather(
            catalog.load(tenant_id="tenant-a", connector_id="new-calendar"),
            google._token("tenant-a"),
            sharepoint._token("tenant-a"),
            http.fetch_page(
                credential=None, tenant_id="tenant-a",
                scopes=("files.read",), cursor=None,
            ),
        )

        self.assertIs(new_connector, created[0])
        self.assertEqual(new_connector.started, 1)
        self.assertEqual(google_token, "token-for-drive")
        self.assertEqual(sharepoint_token, "token-for-sharepoint")
        self.assertEqual(
            http_client.calls[0][2]["headers"]["Authorization"],
            "Bearer token-for-http",
        )
        self.assertEqual(
            [call["required_scopes"] for call in secret_store.calls],
            [("drive.readonly",), ("sites.read.all",), ("files.read",)],
        )
        self.assertEqual(
            await catalog.loaded_ids(tenant_id="tenant-a"), ("new-calendar",),
        )

        # Unloading the newly introduced connector must not close or unregister
        # any of the pre-existing provider instances.
        self.assertTrue(await catalog.unload(
            tenant_id="tenant-a", connector_id="new-calendar",
        ))
        self.assertEqual(new_connector.closed, 1)
        self.assertEqual(await catalog.loaded_ids(tenant_id="tenant-a"), ())

        await asyncio.gather(
            google._token("tenant-a"),
            sharepoint._token("tenant-a"),
            http.fetch_page(
                credential=None, tenant_id="tenant-a",
                scopes=("files.read",), cursor=None,
            ),
        )
        self.assertEqual(len(secret_store.calls), 6)
        self.assertEqual(len(http_client.calls), 2)
        self.assertEqual(
            http_client.calls[1][2]["headers"]["Authorization"],
            "Bearer token-for-http",
        )
        self.assertEqual(new_connector.started, 1)
        self.assertEqual(new_connector.closed, 1)


if __name__ == "__main__":
    unittest.main()
