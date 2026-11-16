from __future__ import annotations

import unittest
from uuid import uuid4

from agent_platform.events.outbox_replay import PostgresOutboxReplayStore


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    def transaction(self):
        return self

    async def execute(self, query, params):
        self.calls.append((query, params))
        return FakeCursor(self.rows)


class OutboxReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_is_tenant_scoped_and_keeps_event_identity(self):
        event_id = uuid4()
        connection = FakeConnection([{"event_id": event_id}])
        store = PostgresOutboxReplayStore(lambda: connection)
        replayed = await store.requeue_published(
            tenant_id="tenant-a", event_ids=[str(event_id)], actor_id="operator-a",
            confirmation="REPLAY-OUTBOX:tenant-a",
        )
        self.assertEqual(replayed, (str(event_id),))
        query, params = connection.calls[0]
        self.assertIn("tenant_id=%s", query)
        self.assertIn("published_at IS NOT NULL", query)
        self.assertEqual(params[0], "tenant-a")

    async def test_requires_confirmation_and_bounded_unique_uuid_set(self):
        store = PostgresOutboxReplayStore(lambda: FakeConnection([]))
        event_id = str(uuid4())
        with self.assertRaises(PermissionError):
            await store.requeue_published(
                tenant_id="tenant-a", event_ids=[event_id], actor_id="operator-a",
                confirmation="yes",
            )
        with self.assertRaises(ValueError):
            await store.requeue_published(
                tenant_id="tenant-a", event_ids=[event_id, event_id],
                actor_id="operator-a", confirmation="REPLAY-OUTBOX:tenant-a",
            )
