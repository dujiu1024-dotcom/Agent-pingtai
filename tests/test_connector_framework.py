from __future__ import annotations

import unittest

from agent_platform.connectors.base import (
    BaseConnector,
    ConnectorAuthenticationError,
    SecretStoreConnectorAuthenticator,
)
from agent_platform.control_plane.data_connectors import DataConnection, DataConnectorService, InMemoryConnectorRepository
from agent_platform.control_plane.http_connector_provider import (
    HTTPJSONConnectorProvider,
    JSONHTTPResponse,
)
from agent_platform.knowledge.google_drive_connector import GoogleDriveKnowledgeConnector
from agent_platform.knowledge.sharepoint_connector import SharePointKnowledgeConnector


class FakeSecretStore:
    def __init__(self, value="short-lived-token"):
        self.value = value
        self.calls = []

    async def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return self.value


class FakeHTTPClient:
    def __init__(self):
        self.calls = []

    async def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return JSONHTTPResponse(200, b'{"items":[],"has_more":false}')


class FakeLegacyCredentials:
    async def resolve(self, secret_ref):
        return "legacy-token"


class ConnectorFrameworkTests(unittest.IsolatedAsyncioTestCase):
    def make_authenticator(self, store):
        return SecretStoreConnectorAuthenticator(
            secret_store=store, consumer_type="agent",
            consumer_id="agent-17", actor_id="system:connector-worker",
        )

    async def test_secret_store_authentication_binds_tenant_consumer_actor_and_scopes(self):
        store = FakeSecretStore()
        auth = self.make_authenticator(store)
        connector = BaseConnector(
            connector_id="drive", credential_ref="secret://tenant-a/credentials/cred-1",
            authenticator=auth, auth_scopes=("files.read", "files.list"),
        )
        token = await connector.authenticate(tenant_id="tenant-a")
        self.assertEqual(token, "short-lived-token")
        self.assertEqual(store.calls, [{
            "tenant_id": "tenant-a", "credential_id": "cred-1",
            "consumer_type": "agent", "consumer_id": "agent-17",
            "required_scopes": ("files.read", "files.list"),
            "actor_id": "system:connector-worker",
        }])

    async def test_secret_store_authentication_rejects_cross_tenant_and_bad_scope(self):
        store = FakeSecretStore()
        auth = self.make_authenticator(store)
        connector = BaseConnector(
            connector_id="drive", credential_ref="secret://tenant-b/credentials/cred-1",
            authenticator=auth, auth_scopes=("files.read",),
        )
        with self.assertRaises(ConnectorAuthenticationError):
            await connector.authenticate(tenant_id="tenant-a")
        self.assertEqual(store.calls, [])

    async def test_secret_store_failure_is_sanitized_and_fail_closed(self):
        class DeniedStore:
            async def resolve(self, **kwargs):
                raise PermissionError("secret token leaked in provider exception")

        connector = BaseConnector(
            connector_id="drive", credential_ref="secret://tenant-a/credentials/cred-1",
            authenticator=self.make_authenticator(DeniedStore()), auth_scopes=("files.read",),
        )
        with self.assertRaisesRegex(ConnectorAuthenticationError, "resolution denied") as raised:
            await connector.authenticate(tenant_id="tenant-a")
        self.assertNotIn("secret token", str(raised.exception))

    async def test_three_existing_connectors_share_base_authentication(self):
        store = FakeSecretStore()
        auth = self.make_authenticator(store)
        google = GoogleDriveKnowledgeConnector(
            client=object(), credentials=FakeLegacyCredentials(),
            credential_ref="secret://tenant-a/credentials/drive",
            document_factory=lambda *args: None, authenticator=auth,
            auth_scopes=("drive.readonly",),
        )
        sharepoint = SharePointKnowledgeConnector(
            client=object(), credentials=FakeLegacyCredentials(),
            credential_ref="secret://tenant-a/credentials/sharepoint",
            drive_id="drive-1", document_factory=lambda *args: None,
            credential_revoker=lambda _ref: None, authenticator=auth,
            auth_scopes=("sites.read.all",),
        )
        http_client = FakeHTTPClient()
        http = HTTPJSONConnectorProvider(
            list_endpoint="https://connector.example.test/files",
            revoke_endpoint="https://connector.example.test/revoke",
            allowed_hosts=frozenset({"connector.example.test"}), client=http_client,
            credential_ref="secret://tenant-a/credentials/http",
            authenticator=auth, auth_scopes=("files.read",),
        )
        self.assertIsInstance(google, BaseConnector)
        self.assertIsInstance(sharepoint, BaseConnector)
        self.assertIsInstance(http, BaseConnector)
        self.assertEqual(await google._token("tenant-a"), "short-lived-token")
        self.assertEqual(await sharepoint._token("tenant-a"), "short-lived-token")
        await http.fetch_page(credential=None, tenant_id="tenant-a", scopes=("files.read",), cursor=None)
        self.assertEqual(len(store.calls), 3)
        self.assertEqual(
            [call["required_scopes"] for call in store.calls],
            [("drive.readonly",), ("sites.read.all",), ("files.read",)],
        )
        self.assertEqual(http_client.calls[0][2]["headers"]["Authorization"], "Bearer short-lived-token")

    async def test_data_connector_service_uses_shared_auth_for_migrated_provider(self):
        store = FakeSecretStore()
        auth = self.make_authenticator(store)
        client = FakeHTTPClient()
        provider = HTTPJSONConnectorProvider(
            list_endpoint="https://connector.example.test/files",
            revoke_endpoint="https://connector.example.test/revoke",
            allowed_hosts=frozenset({"connector.example.test"}), client=client,
            credential_ref="secret://tenant-a/credentials/http",
            authenticator=auth, auth_scopes=("files.read",),
        )
        repository = InMemoryConnectorRepository()
        await repository.add(DataConnection(
            tenant_id="tenant-a", connection_id="http-1", provider="http",
            secret_ref="secret://tenant-a/credentials/http", scopes=("files.read",),
        ))

        class Sink:
            async def upsert_page(self, **kwargs):
                self.last_page = kwargs
            async def revoke_connection(self, **kwargs):
                self.last_revocation = kwargs

        class MustNotResolveLegacySecrets:
            async def resolve(self, secret_ref):
                raise AssertionError("legacy resolver must not bypass BaseConnector auth")

        service = DataConnectorService(
            repository=repository, providers={"http": provider},
            secrets=MustNotResolveLegacySecrets(), sink=Sink(),
        )
        self.assertEqual(await service.sync_once(tenant_id="tenant-a", connection_id="http-1"), 0)
        self.assertEqual(len(store.calls), 1)
        self.assertEqual(
            client.calls[0][2]["headers"]["Authorization"], "Bearer short-lived-token",
        )

    async def test_legacy_resolver_path_remains_compatible(self):
        connector = BaseConnector(
            connector_id="legacy", credential_ref="secret://tenant-a/credentials/old",
            credentials=FakeLegacyCredentials(),
        )
        self.assertEqual(await connector.authenticate(tenant_id="tenant-a"), "legacy-token")

    async def test_shared_auth_requires_scopes_and_reference(self):
        store = FakeSecretStore()
        auth = self.make_authenticator(store)
        with self.assertRaises(ValueError):
            BaseConnector(connector_id="invalid", authenticator=auth)
        with self.assertRaises(ConnectorAuthenticationError):
            connector = BaseConnector(connector_id="missing", credentials=None)
            await connector.authenticate(tenant_id="tenant-a")


if __name__ == "__main__":
    unittest.main()
