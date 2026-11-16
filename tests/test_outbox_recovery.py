from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.events.outbox import PostgresOutboxStore


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeConnection:
    def __init__(self):
        self.query = None
        self.params = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, params):
        self.query = query
        self.params = params
        return FakeCursor([{"outbox_id": 1}, {"outbox_id": 2}])


class OutboxRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.connection = FakeConnection()

        async def connect():
            return self.connection

        self.store = PostgresOutboxStore(connection_factory=connect)
        self.since = datetime(2026, 11, 1, tzinfo=timezone.utc)
        self.until = datetime(2026, 11, 2, tzinfo=timezone.utc)

    async def test_requeues_only_confirmed_bounded_window(self):
        count = await self.store.requeue_published(
            since=self.since,
            until=self.until,
            tenant_id="tenant-a",
            limit=25,
            confirmation="REPLAY_PUBLISHED_OUTBOX",
        )
        self.assertEqual(count, 2)
        self.assertIn("published_at >= %s AND published_at < %s", self.connection.query)
        self.assertIn("FOR UPDATE SKIP LOCKED", self.connection.query)
        self.assertIn("published_at = NULL", self.connection.query)
        self.assertEqual(self.connection.params, (
            self.since, self.until, "tenant-a", "tenant-a", 25,
        ))

    async def test_requires_explicit_confirmation_and_aware_ordered_window(self):
        with self.assertRaisesRegex(ValueError, "confirmation"):
            await self.store.requeue_published(
                since=self.since, until=self.until, confirmation="yes",
            )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            await self.store.requeue_published(
                since=datetime(2026, 11, 1), until=self.until,
                confirmation="REPLAY_PUBLISHED_OUTBOX",
            )
        with self.assertRaisesRegex(ValueError, "earlier"):
            await self.store.requeue_published(
                since=self.until, until=self.since,
                confirmation="REPLAY_PUBLISHED_OUTBOX",
            )
        self.assertIsNone(self.connection.query)

    async def test_rejects_invalid_tenant_and_limits_before_connecting(self):
        for tenant_id, limit in (("", 10), ("tenant-a", True), ("tenant-a", 0), ("tenant-a", 10001)):
            with self.subTest(tenant_id=tenant_id, limit=limit):
                with self.assertRaises(ValueError):
                    await self.store.requeue_published(
                        since=self.since, until=self.until,
                        tenant_id=tenant_id, limit=limit,
                        confirmation="REPLAY_PUBLISHED_OUTBOX",
                    )
        self.assertIsNone(self.connection.query)


if __name__ == "__main__":
    unittest.main()
