from __future__ import annotations

import unittest

from agent_platform.control_plane.connector_sync import ConnectorSyncWorker
from agent_platform.control_plane.data_connectors import (
    ConnectorPage, DataConnection, DataConnectorService, InMemoryConnectorRepository,
)


class Provider:
    def __init__(self):
        self.fail_once = True
        self.calls = 0

    async def fetch_page(self, *, credential, scopes, cursor):
        self.calls += 1
        if self.fail_once:
            self.fail_once = False
            raise TimeoutError("provider temporarily unavailable")
        return ConnectorPage(({"id": "item-1"},), {"page": 2}, False)

    async def revoke(self, *, credential):
        return None


class Secrets:
    async def resolve(self, secret_ref):
        return "short-lived-token"


class Sink:
    def __init__(self):
        self.items = []
        self.upsert_calls = []

    async def upsert_page(self, *, tenant_id, connection_id, items):
        self.items.extend(items)
        self.upsert_calls.append((tenant_id, connection_id, items))

    async def revoke_connection(self, *, tenant_id, connection_id, disposition="retain"):
        pass


class AuditSink:
    def __init__(self, *, fail=False):
        self.events = []
        self.fail = fail

    async def append(self, event):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)

    async def upsert_page(self, *, tenant_id, connection_id, items):
        self.items.extend(items)

    async def revoke_connection(self, *, tenant_id, connection_id, disposition):
        return None


class Queue:
    def __init__(self):
        self.job = None
        self.dead = False

    async def enqueue(self, *, tenant_id, connection_id):
        self.job = {
            "tenant_id": tenant_id, "connection_id": connection_id,
            "attempts": 0, "max_attempts": 3, "lease_owner": None,
        }

    async def claim(self, *, worker_id, lease_seconds=120):
        if self.job is None or self.dead:
            return None
        self.job["attempts"] += 1
        self.job["lease_owner"] = worker_id
        return replace_job(self.job)

    async def heartbeat(self, job, *, lease_seconds=120):
        return self.job is not None and self.job["lease_owner"] == job.lease_owner

    async def complete(self, job):
        self.job = None

    async def fail(self, job, *, error_code):
        self.last_error = error_code
        if job.attempts >= job.max_attempts:
            self.dead = True
        else:
            self.job["lease_owner"] = None
        return self.dead


def replace_job(row):
    from agent_platform.control_plane.connector_sync import ConnectorSyncJob
    return ConnectorSyncJob(
        tenant_id=row["tenant_id"], job_id="sync-job-1",
        connection_id=row["connection_id"], attempts=row["attempts"],
        max_attempts=row["max_attempts"], lease_owner=row["lease_owner"],
    )


class ConnectorSyncWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_provider_failure_is_retried_and_cursor_advances(self):
        repo = InMemoryConnectorRepository()
        await repo.add(DataConnection(
            tenant_id="tenant-a", connection_id="drive-a", provider="drive",
            secret_ref="secret://tenant-a/drive-a", scopes=("files.read",),
        ))
        provider = Provider()
        sink = Sink()
        queue = Queue()
        service = DataConnectorService(
            repository=repo, providers={"drive": provider}, secrets=Secrets(),
            sink=sink, sync_queue=queue,
        )
        await service.request_sync(tenant_id="tenant-a", connection_id="drive-a")
        async def resolve_service(tenant_id):
            self.assertEqual(tenant_id, "tenant-a")
            return service

        audit = AuditSink()
        worker = ConnectorSyncWorker(
            queue=queue, resolve_service=resolve_service,
            worker_id="sync-worker", audit_sink=audit, lease_seconds=5,
        )
        self.assertTrue(await worker.run_once())
        self.assertIsNotNone(queue.job)
        self.assertEqual(queue.last_error, "connectorerror")
        self.assertTrue(await worker.run_once())
        self.assertIsNone(queue.job)
        saved = await repo.get("tenant-a", "drive-a")
        self.assertEqual(saved.cursor, {"page": 2})
        self.assertEqual(len(sink.items), 1)
        self.assertEqual(provider.calls, 2)
        self.assertEqual([event.outcome for event in audit.events], [
            "started", "failed", "started", "succeeded",
        ])
        self.assertTrue(all(event.actor_id == "system:connector-sync-worker" for event in audit.events))
        self.assertTrue(all(event.tenant_id == "tenant-a" for event in audit.events))
        self.assertNotIn("short-lived-token", repr(audit.events))

    async def test_revoked_connection_skips_queued_sync_without_provider_or_retry(self):
        repo = InMemoryConnectorRepository()
        await repo.add(DataConnection(
            tenant_id="tenant-a", connection_id="drive-revoked", provider="drive",
            secret_ref="secret://tenant-a/drive-revoked", scopes=("files.read",),
            status="revoked",
        ))
        provider = Provider()
        sink = Sink()
        queue = Queue()
        await queue.enqueue(tenant_id="tenant-a", connection_id="drive-revoked")
        service = DataConnectorService(
            repository=repo, providers={"drive": provider}, secrets=Secrets(),
            sink=sink, sync_queue=queue,
        )

        async def resolve_service(tenant_id):
            self.assertEqual(tenant_id, "tenant-a")
            return service

        audit = AuditSink()
        worker = ConnectorSyncWorker(
            queue=queue, resolve_service=resolve_service,
            worker_id="sync-worker", audit_sink=audit, lease_seconds=5,
        )

        self.assertTrue(await worker.run_once())
        self.assertIsNone(queue.job)
        self.assertFalse(queue.dead)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(sink.items, [])
        self.assertEqual(
            [(event.outcome, event.error_type) for event in audit.events],
            [("skipped", "connection_inactive")],
        )

    async def test_request_sync_rejects_revoked_connections(self):
        repo = InMemoryConnectorRepository()
        await repo.add(DataConnection(
            tenant_id="tenant-a", connection_id="drive-a", provider="drive",
            secret_ref="secret://tenant-a/drive-a", scopes=("files.read",),
            status="revoked",
        ))
        queue = Queue()
        service = DataConnectorService(
            repository=repo, providers={"drive": Provider()}, secrets=Secrets(),
            sink=Sink(), sync_queue=queue,
        )
        with self.assertRaisesRegex(Exception, "not active"):
            await service.request_sync(tenant_id="tenant-a", connection_id="drive-a")
        self.assertIsNone(queue.job)


    async def test_write_ahead_audit_failure_blocks_provider_side_effect(self):
        repo = InMemoryConnectorRepository()
        await repo.add(DataConnection(
            tenant_id="tenant-a", connection_id="drive-a", provider="drive",
            secret_ref="secret://tenant-a/drive-a", scopes=("files.read",),
        ))
        provider = Provider()
        queue = Queue()
        service = DataConnectorService(
            repository=repo, providers={"drive": provider}, secrets=Secrets(),
            sink=Sink(), sync_queue=queue,
        )

        async def resolve_service(tenant_id):
            return service

        worker = ConnectorSyncWorker(
            queue=queue, resolve_service=resolve_service,
            worker_id="sync-worker", audit_sink=AuditSink(fail=True), lease_seconds=5,
        )
        await service.request_sync(tenant_id="tenant-a", connection_id="drive-a")
        self.assertTrue(await worker.run_once())
        self.assertEqual(provider.calls, 0)
        self.assertEqual(queue.last_error, "runtimeerror")

    async def test_success_audit_failure_does_not_retry_completed_provider_sync(self):
        class FailSuccessAuditSink(AuditSink):
            async def append(self, event):
                if event.outcome == "succeeded":
                    raise RuntimeError("audit store unavailable")
                await super().append(event)

        repo = InMemoryConnectorRepository()
        await repo.add(DataConnection(
            tenant_id="tenant-a", connection_id="drive-a", provider="drive",
            secret_ref="secret://tenant-a/drive-a", scopes=("files.read",),
        ))
        provider = Provider()
        provider.fail_once = False  # 让 provider 第一次就成功，测试审计失败场景
        queue = Queue()
        service = DataConnectorService(
            repository=repo, providers={"drive": provider}, secrets=Secrets(),
            sink=Sink(), sync_queue=queue,
        )

        async def resolve_service(tenant_id):
            return service

        worker = ConnectorSyncWorker(
            queue=queue, resolve_service=resolve_service,
            worker_id="sync-worker", audit_sink=FailSuccessAuditSink(), lease_seconds=5,
        )
        await service.request_sync(tenant_id="tenant-a", connection_id="drive-a")
        self.assertTrue(await worker.run_once())
        self.assertEqual(provider.calls, 1)
        self.assertIsNone(queue.job)

if __name__ == "__main__":
    unittest.main()
