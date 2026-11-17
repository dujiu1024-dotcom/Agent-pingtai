from __future__ import annotations

import unittest

from agent_platform.control_plane.connector_workers import (
    DataConnectorSyncWorker,
    InMemoryConnectorSyncQueue,
)
from agent_platform.control_plane.data_connectors import (
    ConnectorPage,
    DataConnection,
    DataConnectorService,
    InMemoryConnectorRepository,
)


class PagedProvider:
    def __init__(self):
        self.pages = [
            ConnectorPage(({"id": "one", "text": "1"},), {"page": 2}, True),
            ConnectorPage(({"id": "two", "text": "2"},), {"page": 3}, False),
        ]
        self.fail_revoke = False
        self.revoke_calls = 0

    async def fetch_page(self, *, credential, scopes, cursor):
        return self.pages.pop(0)

    async def revoke(self, *, credential):
        self.revoke_calls += 1
        if self.fail_revoke:
            self.fail_revoke = False
            raise RuntimeError("remote provider unavailable")


class Secrets:
    async def resolve(self, secret_ref):
        return "runtime-token"


class Sink:
    def __init__(self):
        self.documents = {}
        self.revocations = []

    async def upsert_page(self, *, tenant_id, connection_id, items):
        for item in items:
            self.documents[(tenant_id, connection_id, item["id"])] = dict(item)

    async def revoke_connection(self, *, tenant_id, connection_id, disposition):
        self.revocations.append((tenant_id, connection_id, disposition))
        if disposition == "delete":
            self.documents = {
                key: value for key, value in self.documents.items()
                if key[:2] != (tenant_id, connection_id)
            }


class ConnectorWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryConnectorRepository()
        self.provider = PagedProvider()
        self.sink = Sink()
        self.queue = InMemoryConnectorSyncQueue()
        self.service = DataConnectorService(
            repository=self.repository, providers={"drive": self.provider},
            secrets=Secrets(), sink=self.sink, sync_queue=self.queue,
        )
        await self.repository.add(DataConnection(
            tenant_id="tenant-a", connection_id="drive-1", provider="drive",
            secret_ref="secret://tenant-a/drive", scopes=("files.read",),
        ))
        self.worker = DataConnectorSyncWorker(
            service=self.service, queue=self.queue, worker_id="connector-worker",
            max_retry_delay_seconds=0,
        )

    async def test_worker_follows_all_pages_until_cursor_is_exhausted(self):
        await self.service.schedule_sync(tenant_id="tenant-a", connection_id="drive-1")
        self.assertTrue(await self.worker.process_once())
        self.assertTrue(await self.worker.process_once())
        saved = await self.repository.get("tenant-a", "drive-1")
        self.assertEqual(saved.cursor, {"page": 3})
        self.assertEqual(set(self.sink.documents), {
            ("tenant-a", "drive-1", "one"),
            ("tenant-a", "drive-1", "two"),
        })
        self.assertFalse(await self.worker.process_once())

    async def test_revocation_cleanup_is_retried_until_remote_and_sink_complete(self):
        await self.service.sync_once(tenant_id="tenant-a", connection_id="drive-1")
        self.provider.fail_revoke = True
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a", connection_id="drive-1",
            revocation_disposition="delete",
        ))
        saved = await self.repository.get("tenant-a", "drive-1")
        self.assertEqual(saved.status, "revoked")
        self.assertTrue(saved.last_error)
        self.assertTrue(await self.worker.process_once())
        saved = await self.repository.get("tenant-a", "drive-1")
        self.assertEqual(saved.status, "revoked")
        self.assertIsNone(saved.last_error)
        self.assertEqual(self.sink.documents, {})
        self.assertEqual(self.provider.revoke_calls, 2)


if __name__ == "__main__":
    unittest.main()
