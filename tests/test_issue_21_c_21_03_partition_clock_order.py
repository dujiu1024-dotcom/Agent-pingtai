"""Supplemental event-consumer fault cases: partition, clock skew and reordering."""
import json
import unittest
from contextlib import asynccontextmanager
from uuid import uuid4

from agent_platform.events.consumer import EventEnvelope, RedisStreamsConsumer
from agent_platform.events.receipts import EventReceiptClaim, PostgresEventReceiptStore


class _Transport:
    def __init__(self, messages=(), partition=False):
        self.messages = list(messages)
        self.partition = partition
        self.acked = []

    async def claim_pending(self, **_kwargs):
        if self.partition:
            raise ConnectionError("injected Redis network partition")
        return []

    async def read_group(self, **_kwargs):
        if self.partition:
            raise ConnectionError("injected Redis network partition")
        rows, self.messages = self.messages, []
        return rows

    async def acknowledge(self, *, message_id, **_kwargs):
        self.acked.append(message_id)
        return 1

    async def dead_letter(self, **_kwargs):
        return "dead-letter-id"

    async def pending_summary(self, **_kwargs):
        return {"pending": len(self.messages)}


class _Receipts:
    def __init__(self):
        self.completed = set()
        self.claimed = set()

    async def claim(self, *, tenant_id, consumer_name, event_id):
        key = (tenant_id, consumer_name, event_id)
        if key in self.completed or key in self.claimed:
            return None
        self.claimed.add(key)
        return EventReceiptClaim(tenant_id, consumer_name, event_id, str(uuid4()), 1)

    async def is_completed(self, *, tenant_id, consumer_name, event_id):
        return (tenant_id, consumer_name, event_id) in self.completed

    async def complete(self, claim):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        self.claimed.discard(key)
        self.completed.add(key)
        return True

    async def fail(self, claim, error):
        self.claimed.discard((claim.tenant_id, claim.consumer_name, claim.event_id))
        return True

    async def dead_letter(self, claim, error):
        self.claimed.discard((claim.tenant_id, claim.consumer_name, claim.event_id))
        return True


def _message(event_id, occurred_at, message_id):
    return message_id, {
        "event_id": event_id,
        "payload": json.dumps({"occurred_at": occurred_at, "value": message_id}),
    }


class EventFaultBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_network_partition_leaves_handler_uninvoked_and_surfaces_failure(self):
        invoked = []
        transport = _Transport(partition=True)
        consumer = RedisStreamsConsumer(
            transport=transport, receipts=_Receipts(), tenant_id="tenant-a",
            topic="run.updated", group="workers", consumer_name="worker-a",
            handler=lambda event: _record(invoked, event),
        )
        with self.assertRaisesRegex(ConnectionError, "network partition"):
            await consumer.consume_batch(count=10, block_ms=0, pending_idle_ms=0)
        self.assertEqual(invoked, [])
        self.assertEqual(transport.acked, [])

    async def test_out_of_order_events_are_idempotent_and_not_sorted_by_client_time(self):
        older_id, newer_id = str(uuid4()), str(uuid4())
        # A skewed producer clock makes the newer event appear older in wall time.
        messages = [
            _message(newer_id, "2036-01-01T00:00:00Z", "redis-1"),
            _message(older_id, "2001-01-01T00:00:00Z", "redis-2"),
        ]
        transport = _Transport(messages=messages)
        receipts = _Receipts()
        handled = []

        async def handler(event: EventEnvelope):
            handled.append(event.event_id)

        consumer = RedisStreamsConsumer(
            transport=transport, receipts=receipts, tenant_id="tenant-a",
            topic="run.updated", group="workers", consumer_name="worker-a",
            handler=handler,
        )
        result = await consumer.consume_batch(count=10, block_ms=0, pending_idle_ms=0)
        self.assertEqual(result["processed"], 2)
        self.assertEqual(handled, [newer_id, older_id])
        self.assertEqual(transport.acked, ["redis-1", "redis-2"])

        transport.messages = [_message(newer_id, "1999-01-01T00:00:00Z", "redis-3")]
        duplicate = await consumer.consume_batch(count=10, block_ms=0, pending_idle_ms=0)
        self.assertEqual(duplicate["duplicates"], 1)
        self.assertEqual(handled, [newer_id, older_id])
        self.assertIn("redis-3", transport.acked)


async def _record(target, event):
    target.append(event.event_id)


class _Cursor:
    async def fetchone(self):
        return {"attempts": 1}


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _Connection:
    def __init__(self):
        self.sql = ""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def transaction(self):
        return _Transaction()

    async def execute(self, sql, params):
        self.sql = sql
        self.params = params
        return _Cursor()


class PostgresLeaseClockTests(unittest.IsolatedAsyncioTestCase):
    async def test_receipt_lease_uses_database_clock_not_worker_wall_clock(self):
        connection = _Connection()
        store = PostgresEventReceiptStore(lambda: _resolved(connection), lease_seconds=5)
        claim = await store.claim(
            tenant_id="tenant-a", consumer_name="worker-a", event_id=str(uuid4()),
        )
        self.assertIsNotNone(claim)
        self.assertIn("now()", connection.sql.lower())
        self.assertIn("make_interval", connection.sql.lower())
        self.assertNotIn("issued_at", connection.sql.lower())


async def _resolved(value):
    return value


if __name__ == "__main__":
    unittest.main()
