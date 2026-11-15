from __future__ import annotations

import unittest

from agent_platform.control_plane.data_connectors import (
    ConnectorError, ConnectorPage, DataConnection, DataConnectorService,
    InMemoryConnectorRepository,
)


class FakeProvider:
    def __init__(self):
        self.pages = [
            ConnectorPage(({"id": "doc-1", "text": "one"},), {"page": 2}, True),
            ConnectorPage(({"id": "doc-2", "text": "two"},), {"page": 3}, False),
        ]
        self.revoked = False

    async def fetch_page(self, *, credential, scopes, cursor):
        self.assert_credential = credential
        self.last_cursor = cursor
        return self.pages.pop(0)

    async def revoke(self, *, credential):
        self.revoked = True


class FakeSecrets:
    async def resolve(self, secret_ref):
        if secret_ref != "secret://tenant-a/source":
            raise ValueError("unknown secret reference")
        return "runtime-only-token"


class FakeSink:
    def __init__(self):
        self.items = {}
        self.revocations = []

    async def upsert_page(self, *, tenant_id, connection_id, items):
        for item in items:
            # Idempotency key is stable across page replay.
            self.items[(tenant_id, connection_id, item["id"])] = dict(item)

    async def revoke_connection(self, *, tenant_id, connection_id, disposition):
        self.revocations.append((tenant_id, connection_id, disposition))
        if disposition == "delete":
            self.items = {
                key: value for key, value in self.items.items()
                if key[:2] != (tenant_id, connection_id)
            }


class DataConnectorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = InMemoryConnectorRepository()
        self.provider = FakeProvider()
        self.sink = FakeSink()
        self.service = DataConnectorService(
            repository=self.repo, providers={"drive": self.provider},
            secrets=FakeSecrets(), sink=self.sink,
        )
        await self.repo.add(DataConnection(
            tenant_id="tenant-a", connection_id="drive-1", provider="drive",
            secret_ref="secret://tenant-a/source", scopes=("files.read",),
        ))

    async def test_incremental_sync_advances_cursor_and_is_idempotent(self):
        self.assertEqual(await self.service.sync_once(
            tenant_id="tenant-a", connection_id="drive-1",
        ), 1)
        saved = await self.repo.get("tenant-a", "drive-1")
        self.assertEqual(saved.cursor, {"page": 2})
        self.assertEqual(saved.version, 1)
        self.assertEqual(await self.service.sync_once(
            tenant_id="tenant-a", connection_id="drive-1",
        ), 1)
        self.assertEqual(len(self.sink.items), 2)

    async def test_revocation_stops_future_sync_and_applies_delete_policy(self):
        await self.service.sync_once(tenant_id="tenant-a", connection_id="drive-1")
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a", connection_id="drive-1",
            revocation_disposition="delete",
        ))
        self.assertTrue(self.provider.revoked)
        self.assertEqual(len(self.sink.items), 0)
        with self.assertRaises(ConnectorError):
            await self.service.sync_once(
                tenant_id="tenant-a", connection_id="drive-1",
            )

    async def test_tenant_isolation(self):
        with self.assertRaises(ConnectorError):
            await self.service.sync_once(
                tenant_id="tenant-b", connection_id="drive-1",
            )
        self.assertIsNone(await self.repo.get("tenant-b", "drive-1"))


if __name__ == "__main__":
    unittest.main()
