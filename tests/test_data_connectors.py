from __future__ import annotations

import unittest

from agent_platform.control_plane.data_connectors import (
    ConnectorCleanupJob,
    ConnectorError,
    ConnectorPage,
    DataConnection,
    DataConnectorService,
    InMemoryConnectorRepository,
)


class FakeProvider:
    def __init__(self):
        self.pages = [
            ConnectorPage(({"id": "doc-1", "text": "one"},), {"page": 2}, True),
            ConnectorPage(({"id": "doc-2", "text": "two"},), {"page": 3}, False),
        ]
        self.revoked = False
        self.fail_revoke = False

    async def fetch_page(self, *, credential, scopes, cursor):
        self.assert_credential = credential
        self.last_cursor = cursor
        return self.pages.pop(0)

    async def revoke(self, *, credential):
        if self.fail_revoke:
            raise RuntimeError("remote revoke unavailable")
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
        self.fail_revoke_once = False

    async def upsert_page(self, *, tenant_id, connection_id, items):
        for item in items:
            # Idempotency key is stable across page replay.
            self.items[(tenant_id, connection_id, item["id"])] = dict(item)

    async def revoke_connection(self, *, tenant_id, connection_id, disposition):
        self.revocations.append((tenant_id, connection_id, disposition))
        if self.fail_revoke_once:
            self.fail_revoke_once = False
            raise RuntimeError("sink cleanup unavailable")
        if disposition == "delete":
            self.items = {
                key: value for key, value in self.items.items()
                if key[:2] != (tenant_id, connection_id)
            }



class FakeCleanupQueue:
    def __init__(self):
        self.job = None
        self.completed = []
        self.failures = []
    async def enqueue(self, *, tenant_id, connection_id, disposition):
        self.job = ConnectorCleanupJob(
            tenant_id, "cleanup-1", connection_id, disposition,
            False, False, 0, 3, "worker-a",
        )
    async def claim(self, *, worker_id, lease_seconds=120):
        if self.job is None:
            return None
        from dataclasses import replace
        self.job = replace(self.job, attempts=self.job.attempts + 1, lease_owner=worker_id)
        return self.job
    async def mark_remote_revoked(self, job):
        from dataclasses import replace
        self.job = replace(self.job, remote_revoked=True)
    async def mark_sink_cleaned(self, job):
        from dataclasses import replace
        self.job = replace(self.job, sink_cleaned=True)
    async def complete(self, job):
        self.completed.append(job.connection_id)
        self.job = None
    async def fail(self, job, *, error_code):
        self.failures.append(error_code)
        from dataclasses import replace
        self.job = replace(self.job or job, lease_owner="worker-a")
        return False


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

    async def test_connection_registration_requires_secret_reference(self):
        connection = await self.service.create_connection(
            tenant_id="tenant-a", connection_id="drive-2", provider="drive",
            display_name="Shared Drive", secret_ref="secret://tenant-a/drive-2",
            scopes=("files.read", " files.read "), actor_id="user-a",
        )
        self.assertEqual(connection.display_name, "Shared Drive")
        self.assertEqual(connection.scopes, ("files.read",))
        with self.assertRaisesRegex(ConnectorError, "tenant-scoped"):
            await self.service.create_connection(
                tenant_id="tenant-a", connection_id="drive-cross-tenant",
                provider="drive", display_name="Unsafe reference",
                secret_ref="secret://tenant-b/drive", scopes=("files.read",),
                actor_id="user-a",
            )
        with self.assertRaises(ConnectorError):
            await self.service.create_connection(
                tenant_id="tenant-a", connection_id="drive-3", provider="drive",
                display_name="Unsafe", secret_ref="plaintext-token",
                scopes=("files.read",), actor_id="user-a",
            )

    async def test_credential_resolution_failure_is_recorded_without_leaking_secret(self):
        class BrokenSecrets:
            async def resolve(self, secret_ref):
                raise RuntimeError("sensitive token material")

        service = DataConnectorService(
            repository=self.repo, providers={"drive": self.provider},
            secrets=BrokenSecrets(), sink=self.sink,
        )
        with self.assertRaises(ConnectorError):
            await service.sync_once(tenant_id="tenant-a", connection_id="drive-1")
        saved = await self.repo.get("tenant-a", "drive-1")
        self.assertEqual(saved.last_error, "RuntimeError")
        self.assertNotIn("sensitive", saved.last_error)

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

    async def test_active_cursor_race_does_not_trigger_revocation_cleanup(self):
        from dataclasses import replace

        class ConcurrentRepository(InMemoryConnectorRepository):
            def __init__(self):
                super().__init__()
                self.reads = 0

            async def get(self, tenant_id, connection_id):
                self.reads += 1
                item = await super().get(tenant_id, connection_id)
                if self.reads == 2 and item is not None:
                    item = replace(item, version=item.version + 1)
                    self.records[(tenant_id, connection_id)] = item
                return item

        repository = ConcurrentRepository()
        await repository.add(DataConnection(
            tenant_id="tenant-a", connection_id="drive-race", provider="drive",
            secret_ref="secret://tenant-a/source", scopes=("files.read",),
        ))
        service = DataConnectorService(
            repository=repository, providers={"drive": self.provider},
            secrets=FakeSecrets(), sink=self.sink,
        )
        with self.assertRaisesRegex(ConnectorError, "cursor changed"):
            await service.sync_once(
                tenant_id="tenant-a", connection_id="drive-race",
                revocation_disposition="delete",
            )
        self.assertEqual(self.sink.revocations, [])

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

    async def test_remote_revocation_failure_is_recorded_after_local_revoke(self):
        self.provider.fail_revoke = True
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a", connection_id="drive-1",
        ))
        saved = await self.repo.get("tenant-a", "drive-1")
        self.assertEqual(saved.status, "revoked")
        self.assertIn("remote revoke", saved.last_error)

    async def test_revocation_cleanup_can_be_retried_after_local_revoke(self):
        self.sink.fail_revoke_once = True
        with self.assertRaises(RuntimeError):
            await self.service.revoke(
                tenant_id="tenant-a", connection_id="drive-1",
            )
        saved = await self.repo.get("tenant-a", "drive-1")
        self.assertEqual(saved.status, "revoked")
        self.assertTrue(await self.service.revoke(
            tenant_id="tenant-a", connection_id="drive-1",
        ))
        self.assertEqual(len(self.sink.revocations), 2)

    async def test_revocation_queues_remote_and_sink_cleanup_for_worker(self):
        from agent_platform.control_plane.connector_cleanup import ConnectorCleanupWorker
        queue = FakeCleanupQueue()
        service = DataConnectorService(
            repository=self.repo, providers={"drive": self.provider},
            secrets=FakeSecrets(), sink=self.sink, cleanup_queue=queue,
        )
        self.assertTrue(await service.revoke(
            tenant_id="tenant-a", connection_id="drive-1",
            revocation_disposition="delete",
        ))
        self.assertEqual((await self.repo.get("tenant-a", "drive-1")).status, "revoked")
        self.assertFalse(self.provider.revoked)
        worker = ConnectorCleanupWorker(
            queue=queue, service=service, worker_id="cleanup-worker",
        )
        self.assertTrue(await worker.run_once())
        self.assertTrue(self.provider.revoked)
        self.assertEqual(self.sink.revocations, [("tenant-a", "drive-1", "delete")])
        self.assertEqual(queue.completed, ["drive-1"])

    async def test_cleanup_worker_retries_sink_failure_without_repeating_remote_revoke(self):
        from agent_platform.control_plane.connector_cleanup import ConnectorCleanupWorker
        queue = FakeCleanupQueue()
        self.sink.fail_revoke_once = True
        service = DataConnectorService(
            repository=self.repo, providers={"drive": self.provider},
            secrets=FakeSecrets(), sink=self.sink, cleanup_queue=queue,
        )
        await service.revoke(
            tenant_id="tenant-a", connection_id="drive-1",
            revocation_disposition="delete",
        )
        worker = ConnectorCleanupWorker(
            queue=queue, service=service, worker_id="cleanup-worker",
        )
        await worker.run_once()
        self.assertTrue(queue.job.remote_revoked)
        self.assertFalse(queue.job.sink_cleaned)
        await worker.run_once()
        self.assertEqual(queue.completed, ["drive-1"])
        self.assertEqual(len(self.sink.revocations), 2)

    async def test_remote_revoke_failure_still_cleans_sink_and_schedules_retry(self):
        class FakeRetryQueue:
            def __init__(self):
                self.jobs = []

            async def enqueue(self, **kwargs):
                self.jobs.append(kwargs)

        retry_queue = FakeRetryQueue()
        self.provider.fail_revoke = True
        service = DataConnectorService(
            repository=self.repo, providers={"drive": self.provider},
            secrets=FakeSecrets(), sink=self.sink, sync_queue=retry_queue,
        )

        self.assertTrue(await service.revoke(
            tenant_id="tenant-a", connection_id="drive-1",
            revocation_disposition="delete",
        ))

        saved = await self.repo.get("tenant-a", "drive-1")
        self.assertEqual(saved.status, "revoked")
        self.assertEqual(
            self.sink.revocations,
            [("tenant-a", "drive-1", "delete")],
        )
        self.assertIn("remote revoke", saved.last_error)
        self.assertNotIn("unavailable", saved.last_error)
        self.assertEqual(retry_queue.jobs, [{
            "tenant_id": "tenant-a", "connection_id": "drive-1",
            "job_type": "revoke_cleanup", "disposition": "delete",
        }])

    async def test_missing_provider_still_applies_local_sink_disposition(self):
        service = DataConnectorService(
            repository=self.repo, providers={},
            secrets=FakeSecrets(), sink=self.sink,
        )

        self.assertTrue(await service.revoke(
            tenant_id="tenant-a", connection_id="drive-1",
            revocation_disposition="delete",
        ))

        saved = await self.repo.get("tenant-a", "drive-1")
        self.assertEqual(saved.status, "revoked")
        self.assertEqual(
            self.sink.revocations,
            [("tenant-a", "drive-1", "delete")],
        )
        self.assertIsNone(saved.last_error)

    async def test_tenant_isolation(self):
        with self.assertRaises(ConnectorError):
            await self.service.sync_once(
                tenant_id="tenant-b", connection_id="drive-1",
            )
        self.assertIsNone(await self.repo.get("tenant-b", "drive-1"))


if __name__ == "__main__":
    unittest.main()
