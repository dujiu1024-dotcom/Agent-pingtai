from __future__ import annotations

import unittest
from datetime import datetime, timezone
from uuid import uuid4

from agent_platform.blackboard.replay import (
    BlackboardReplayCursor,
    PostgresBlackboardEventReplay,
)
from agent_platform.contracts import Scope


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False


class FakeConnection:
    def __init__(self, rows):
        self.rows = rows
        self.query = None
        self.params = None

    def __await__(self):
        # Make the connection itself awaitable: `await factory()` that returns
        # this connection works out of the box.
        async def _resolve():
            return self
        return _resolve().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, params):
        self.query = query
        self.params = params
        return FakeCursor(self.rows)


class BlackboardEventReplayTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.timestamp = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)
        self.event_id = str(uuid4())
        self.row = {
            "event_id": self.event_id,
            "event_type": "blackboard.entry_proposed",
            "scope": "task",
            "scope_id": "task-1",
            "entry_id": str(uuid4()),
            "entry_version": 2,
            "actor_id": "agent-1",
            "payload": {"kind": "claim"},
            "correlation_id": str(uuid4()),
            "occurred_at": self.timestamp,
        }
        self.connection = FakeConnection([self.row])

    async def test_replay_is_tenant_scoped_ordered_and_cursor_resumable(self):
        replay = PostgresBlackboardEventReplay(
            tenant_id="tenant-a", connection_factory=lambda: self.connection
        )
        events = await replay.list_after(
            cursor=BlackboardReplayCursor(self.timestamp, str(uuid4())),
            scope=Scope.TASK,
            scope_id="task-1",
            limit=25,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_id, self.event_id)
        self.assertIn("tenant_id = %s", self.connection.query)
        self.assertIn("(occurred_at, event_id) > (%s, %s)", self.connection.query)
        self.assertIn("ORDER BY occurred_at ASC, event_id ASC", self.connection.query)
        self.assertEqual(self.connection.params[0], "tenant-a")
        self.assertEqual(self.connection.params[-3:], ("task", "task-1", 25))

    async def test_latest_cursor_is_scoped_to_tenant_and_resource(self):
        replay = PostgresBlackboardEventReplay(
            tenant_id="tenant-a", connection_factory=lambda: self.connection
        )
        cursor = await replay.latest_cursor(scope=Scope.TASK, scope_id="task-1")
        self.assertEqual(cursor.event_id, self.event_id)
        self.assertIn("tenant_id = %s AND scope = %s AND scope_id = %s", self.connection.query)
        self.assertIn("ORDER BY occurred_at DESC, event_id DESC", self.connection.query)
        self.assertEqual(self.connection.params, ("tenant-a", "task", "task-1"))

    async def test_cursor_for_event_round_trips(self):
        replay = PostgresBlackboardEventReplay(
            tenant_id="tenant-a", connection_factory=lambda: self.connection
        )
        event = (await replay.list_after())[0]
        cursor = replay.cursor_for(event)
        self.assertEqual(cursor.event_id, self.event_id)
        self.assertEqual(cursor.occurred_at, self.timestamp)

    async def test_rejects_ambiguous_scope_and_invalid_limits(self):
        replay = PostgresBlackboardEventReplay(
            tenant_id="tenant-a", connection_factory=lambda: self.connection
        )
        with self.assertRaises(ValueError):
            await replay.list_after(scope=Scope.TASK)
        with self.assertRaises(ValueError):
            await replay.list_after(limit=0)

    async def test_cursor_requires_aware_timestamp_and_uuid(self):
        with self.assertRaises(ValueError):
            BlackboardReplayCursor(datetime(2026, 11, 17), self.event_id)
        with self.assertRaises(ValueError):
            BlackboardReplayCursor(self.timestamp, "not-a-uuid")


if __name__ == "__main__":
    unittest.main()
