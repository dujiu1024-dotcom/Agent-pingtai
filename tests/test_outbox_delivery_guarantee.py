from __future__ import annotations

import unittest
from uuid import UUID

from agent_platform.events.outbox import OutboxRecord, PostgresOutboxStore


class Cursor:
    def __init__(self, rows=()):
        self.rows = list(rows)
    async def fetchall(self):
        return self.rows
    async def fetchone(self):
        return self.rows[0] if self.rows else None


class Transaction:
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False


class Connection:
    def __init__(self):
        self.statements = []
    async def __aenter__(self): return self
    async def __aexit__(self, *args): return False
    def transaction(self): return Transaction()
    async def execute(self, query, params):
        self.statements.append((query, params))
        return Cursor()


class OutboxDeliveryGuaranteeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.connection = Connection()
        async def connect(): return self.connection
        self.store = PostgresOutboxStore(connection_factory=connect)

    async def test_claim_excludes_exhausted_and_dead_lettered_events(self):
        await self.store.claim_batch(limit=10)
        query, _ = self.connection.statements[-1]
        self.assertIn("attempts < max_attempts", query)
        self.assertIn("dead_lettered_at IS NULL", query)
        self.assertIn("FOR UPDATE SKIP LOCKED", query)

    async def test_failed_delivery_moves_exhausted_row_to_dead_letter(self):
        record = OutboxRecord(
            tenant_id="tenant-a", outbox_id=3, event_id="event-3", topic="topic",
            payload={}, lease_token=str(UUID(int=3)),
        )
        await self.store.mark_failed(record, "TimeoutError")
        query, params = self.connection.statements[-1]
        self.assertIn("dead_lettered_at = CASE WHEN attempts >= max_attempts", query)
        self.assertIn("next_attempt_at = CASE WHEN attempts >= max_attempts THEN NULL", query)
        self.assertEqual(params, ("TimeoutError", "tenant-a", 3, UUID(int=3)))


if __name__ == "__main__":
    unittest.main()
