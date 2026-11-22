from __future__ import annotations

import asyncio
import json
import unittest
from datetime import datetime, timezone

from agent_platform.knowledge.acl_invalidation_worker import (
    KnowledgeInvalidationWorker,
    PostgresKnowledgeInvalidationConsumer,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.closed = True
        return False

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        if "SELECT event_id" in query:
            return FakeCursor(self.rows)
        return FakeCursor([])


class KnowledgeInvalidationConsumerTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatches_notifications_and_acknowledges_in_same_transaction(self):
        rows = [{
            "event_id": 7, "tenant_id": "tenant-a", "resource_type": "chunk",
            "resource_id": "00000000-0000-0000-0000-000000000007",
            "reason": "acl_changed", "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc),
        }]
        connection = FakeConnection(rows)
        consumer = PostgresKnowledgeInvalidationConsumer(lambda: _resolved(connection))
        self.assertEqual(await consumer.process_once(), 1)
        notify = next(call for call in connection.calls if "pg_notify" in call[0])
        payload = json.loads(notify[1][1])
        self.assertEqual(payload["tenant_id"], "tenant-a")
        self.assertEqual(payload["resource_type"], "chunk")
        self.assertEqual(payload["reason"], "acl_changed")
        self.assertTrue(any("processed_at = now()" in call[0] for call in connection.calls))
        self.assertTrue(connection.closed)

    async def test_rejects_unbounded_batch(self):
        consumer = PostgresKnowledgeInvalidationConsumer(lambda: None)
        with self.assertRaises(ValueError):
            await consumer.process_once(limit=501)


async def _resolved(value):
    return value


class WorkerLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_starts_and_stops_idempotently(self):
        class Consumer:
            async def process_once(self, *, limit):
                return 0
        worker = KnowledgeInvalidationWorker(
            consumer=Consumer(), interval_seconds=0.1,
        )
        await worker.start()
        await worker.start()
        self.assertTrue(worker.running)
        await worker.stop()
        self.assertFalse(worker.running)


if __name__ == "__main__":
    unittest.main()
