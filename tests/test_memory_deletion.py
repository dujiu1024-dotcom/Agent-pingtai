from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.memory.deletion import (
    MemoryDeletionJob,
    MemoryDeletionWorker,
    PostgresMemoryDeletionQueue,
)


class FakeCursor:
    def __init__(self, *, one=None, rowcount=1):
        self.one = one
        self.rowcount = rowcount

    async def fetchone(self):
        return self.one


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, cursor):
        self.cursor = cursor
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, params=()):
        self.calls.append((" ".join(query.split()), params))
        return self.cursor


class MemoryDeletionQueueContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_is_tenant_scoped_and_idempotent(self):
        connection = FakeConnection(FakeCursor(one={"job_id": "job-1"}))
        async def connect():
            return connection
        queue = PostgresMemoryDeletionQueue(connect)
        self.assertTrue(await queue.enqueue("tenant-a", "memory-a"))
        query, params = connection.calls[0]
        self.assertIn("ON CONFLICT (tenant_id, memory_id) DO NOTHING", query)
        self.assertEqual(params, ("tenant-a", "memory-a"))

    async def test_claim_uses_skip_locked_and_fencing_token(self):
        expiry = datetime.now(timezone.utc)
        connection = FakeConnection(FakeCursor(one={
            "job_id": "job-1", "tenant_id": "tenant-a",
            "memory_id": "memory-a", "attempts": 2,
            "lease_token": "token-2", "lease_expires_at": expiry,
        }))
        async def connect():
            return connection
        queue = PostgresMemoryDeletionQueue(connect)
        job = await queue.claim(worker_id="worker-a", lease_seconds=30)
        self.assertIsNotNone(job)
        self.assertEqual(job.lease_token, "token-2")
        query, params = connection.calls[0]
        self.assertIn("FOR UPDATE SKIP LOCKED", query)
        self.assertIn("lease_token = %s", query)
        self.assertEqual(params[0], "worker-a")
        self.assertEqual(params[2], 30)

    async def test_complete_requires_current_lease_token(self):
        connection = FakeConnection(FakeCursor(rowcount=0))
        async def connect():
            return connection
        queue = PostgresMemoryDeletionQueue(connect)
        self.assertFalse(await queue.complete(job_id="job-1", lease_token="stale"))
        query, params = connection.calls[0]
        self.assertIn("status = 'leased' AND lease_token = %s", query)
        self.assertEqual(params, ("job-1", "stale"))

    async def test_fail_records_only_error_code_and_dead_letters_at_limit(self):
        connection = FakeConnection(FakeCursor(rowcount=1))
        async def connect():
            return connection
        queue = PostgresMemoryDeletionQueue(connect)
        self.assertTrue(await queue.fail(
            job_id="job-1", lease_token="token", attempts=8,
            error_code="ConnectionError", max_attempts=8,
        ))
        query, params = connection.calls[0]
        self.assertIn("status = %s", query)
        self.assertEqual(params[0], "dead")
        self.assertEqual(params[3:], ("job-1", "token"))

    async def test_worker_retries_cleanup_and_never_persists_exception_message(self):
        job = MemoryDeletionJob(
            job_id="job-1", tenant_id="tenant-a", memory_id="memory-a",
            attempts=1, lease_token="token", lease_expires_at=datetime.now(timezone.utc),
        )
        class Queue:
            def __init__(self):
                self.failed = []
                self.completed = []
            async def claim(self, **_kwargs):
                return job
            async def fail(self, **kwargs):
                self.failed.append(kwargs)
                return True
            async def complete(self, **kwargs):
                self.completed.append(kwargs)
                return True
        queue = Queue()
        async def cleanup(_tenant, _memory):
            raise RuntimeError("contains user memory content")
        worker = MemoryDeletionWorker(
            queue=queue, cleanup=cleanup, worker_id="worker-a",
        )
        self.assertTrue(await worker.run_once())
        self.assertEqual(queue.failed[0]["error_code"], "RuntimeError")
        self.assertNotIn("contains user memory content", str(queue.failed))
        self.assertEqual(queue.completed, [])

    async def test_worker_completes_only_after_cleanup(self):
        job = MemoryDeletionJob(
            job_id="job-2", tenant_id="tenant-b", memory_id="memory-b",
            attempts=1, lease_token="token-2", lease_expires_at=datetime.now(timezone.utc),
        )
        events = []
        class Queue:
            async def claim(self, **_kwargs):
                return job
            async def complete(self, **kwargs):
                events.append(("complete", kwargs))
                return True
            async def fail(self, **kwargs):
                events.append(("fail", kwargs))
                return True
        async def cleanup(tenant_id, memory_id):
            events.append(("cleanup", (tenant_id, memory_id)))
        worker = MemoryDeletionWorker(
            queue=Queue(), cleanup=cleanup, worker_id="worker-b",
        )
        self.assertTrue(await worker.run_once())
        self.assertEqual(events[0], ("cleanup", ("tenant-b", "memory-b")))
        self.assertEqual(events[1][0], "complete")


if __name__ == "__main__":
    unittest.main()
