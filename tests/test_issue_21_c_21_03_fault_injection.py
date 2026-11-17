"""Deterministic fault-injection regressions for Issue #21 C-21-03."""

from __future__ import annotations

import asyncio
import json
import unittest
from uuid import uuid4

from agent_platform.events.consumer import (
    EventEnvelope,
    IdempotentEventHandler,
    RedisStreamsConsumer,
    RedisStreamsConsumerWorker,
)
from agent_platform.events.receipts import EventReceiptClaim


class FaultInjectingReceipts:
    def __init__(self):
        self.rows = {}
        self.active = set()
        self.fail_claim_once = False
        self.fail_complete_once = False

    async def claim(self, *, tenant_id, consumer_name, event_id):
        if self.fail_claim_once:
            self.fail_claim_once = False
            raise ConnectionError("simulated database outage")
        key = (tenant_id, consumer_name, event_id)
        row = self.rows.get(key)
        if row and row["status"] in {"completed", "dead_lettered"}:
            return None
        if key in self.active:
            return None
        attempts = row["attempts"] + 1 if row else 1
        self.active.add(key)
        self.rows[key] = {"status": "processing", "attempts": attempts}
        return EventReceiptClaim(tenant_id, consumer_name, event_id, f"lease-{attempts}", attempts)

    async def is_completed(self, *, tenant_id, consumer_name, event_id):
        row = self.rows.get((tenant_id, consumer_name, event_id))
        return bool(row and row["status"] in {"completed", "dead_lettered"})

    async def complete(self, claim):
        if self.fail_complete_once:
            self.fail_complete_once = False
            raise ConnectionError("simulated database commit outage")
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        if key not in self.active:
            return False
        self.active.remove(key)
        self.rows[key]["status"] = "completed"
        return True

    async def fail(self, claim, error):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        self.active.discard(key)
        self.rows[key].update(status="retryable", last_error=error)
        return True

    async def dead_letter(self, claim, error):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        self.active.discard(key)
        self.rows[key].update(status="dead_lettered", last_error=error)
        return True


class FaultInjectingRedis:
    def __init__(self, messages=()):
        self.pending = list(messages)
        self.fresh = []
        self.acked = []
        self.dead_letters = []
        self.fail_claim_once = False
        self.fail_ack_once = False
        self.connected = False

    async def connect(self):
        self.connected = True

    async def close(self):
        self.connected = False

    async def ensure_group(self, **_kwargs):
        return None

    async def claim_pending(self, **_kwargs):
        if self.fail_claim_once:
            self.fail_claim_once = False
            raise ConnectionError("simulated Redis outage")
        return list(self.pending)

    async def read_group(self, **_kwargs):
        rows, self.fresh = self.fresh, []
        return rows

    async def acknowledge(self, *, message_id, **_kwargs):
        if self.fail_ack_once:
            self.fail_ack_once = False
            raise ConnectionError("simulated Redis acknowledgement outage")
        self.acked.append(message_id)
        self.pending = [row for row in self.pending if row[0] != message_id]
        return 1

    async def dead_letter(self, *, message_id, fields, reason, **_kwargs):
        self.dead_letters.append((message_id, dict(fields), reason))
        self.pending = [row for row in self.pending if row[0] != message_id]
        self.acked.append(message_id)
        return "dlq-1"

    async def pending_summary(self, **_kwargs):
        return {"pending": len(self.pending)}


def message(message_id="1-0", event_id=None, payload=None):
    return (
        message_id,
        {
            "event_id": event_id or str(uuid4()),
            "payload": json.dumps(payload or {"operation": "write"}),
        },
    )


class IdempotentEffectStore:
    def __init__(self):
        self.keys = set()
        self.effects = []

    async def execute(self, *, idempotency_key, event):
        if idempotency_key not in self.keys:
            self.keys.add(idempotency_key)
            self.effects.append(dict(event.payload))
        return {"applied": True}


class Issue21C2103FaultInjectionTests(unittest.IsolatedAsyncioTestCase):
    def make_consumer(self, redis, receipts, handler, *, name="worker-a"):
        return RedisStreamsConsumer(
            transport=redis,
            receipts=receipts,
            tenant_id="tenant-a",
            topic="fault-injection",
            group="workers",
            consumer_name=name,
            handler=handler,
            max_attempts=4,
        )

    async def test_database_outage_does_not_ack_or_lose_redis_message(self):
        row = message()
        redis = FaultInjectingRedis([row])
        receipts = FaultInjectingReceipts()
        receipts.fail_claim_once = True
        consumer = self.make_consumer(redis, receipts, self.fail_if_called)

        with self.assertRaisesRegex(ConnectionError, "database outage"):
            await consumer.consume_batch(block_ms=0)
        self.assertEqual(redis.acked, [])
        self.assertEqual(redis.pending, [row])

    async def test_model_timeout_is_retryable_and_event_recovers(self):
        row = message()
        redis = FaultInjectingRedis([row])
        receipts = FaultInjectingReceipts()
        attempts = []

        async def model_handler(event):
            attempts.append(event.event_id)
            if len(attempts) == 1:
                raise TimeoutError("simulated model request timeout")

        consumer = self.make_consumer(redis, receipts, model_handler)
        first = await consumer.consume_batch(block_ms=0)
        self.assertEqual(first["failed"], 1)
        self.assertEqual(redis.acked, [])
        second = await consumer.consume_batch(block_ms=0)
        self.assertEqual(second["processed"], 1)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(redis.acked, [row[0]])

    async def test_worker_restarts_after_redis_outage_without_dropping_poll_loop(self):
        row = message()
        redis = FaultInjectingRedis([row])
        redis.fail_claim_once = True
        handled = asyncio.Event()

        async def handler(_event):
            handled.set()

        worker = RedisStreamsConsumerWorker(
            consumer=self.make_consumer(redis, FaultInjectingReceipts(), handler),
            interval_seconds=0.1,
            batch_size=1,
            block_ms=0,
            pending_idle_ms=0,
        )
        await worker.start()
        try:
            await asyncio.wait_for(handled.wait(), timeout=2)
            self.assertTrue(worker.running)
            self.assertEqual(redis.acked, [row[0]])
        finally:
            await worker.stop()
        self.assertFalse(redis.connected)

    async def test_redis_ack_outage_replay_is_suppressed_by_completed_receipt(self):
        row = message()
        redis = FaultInjectingRedis([row])
        redis.fail_ack_once = True
        receipts = FaultInjectingReceipts()
        handled = []

        async def handler(event):
            handled.append(event.event_id)

        consumer = self.make_consumer(redis, receipts, handler)
        with self.assertRaisesRegex(ConnectionError, "acknowledgement outage"):
            await consumer.consume_batch(block_ms=0)
        self.assertEqual(len(handled), 1)
        self.assertTrue(await receipts.is_completed(
            tenant_id="tenant-a", consumer_name="worker-a", event_id=row[1]["event_id"]
        ))

        result = await consumer.consume_batch(block_ms=0)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(len(handled), 1)
        self.assertEqual(redis.acked, [row[0]])

    async def test_duplicate_stream_deliveries_execute_effect_once(self):
        event_id = str(uuid4())
        redis = FaultInjectingRedis()
        redis.fresh = [message("10-0", event_id), message("11-0", event_id)]
        effects = IdempotentEffectStore()
        handler = IdempotentEventHandler(effects)
        result = await self.make_consumer(redis, FaultInjectingReceipts(), handler).consume_batch(
            block_ms=0
        )
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(len(effects.effects), 1)
        self.assertEqual(redis.acked, ["10-0", "11-0"])

    async def test_database_completion_outage_replay_keeps_external_effect_idempotent(self):
        row = message()
        redis = FaultInjectingRedis([row])
        receipts = FaultInjectingReceipts()
        receipts.fail_complete_once = True
        effects = IdempotentEffectStore()
        consumer = self.make_consumer(redis, receipts, IdempotentEventHandler(effects))

        with self.assertRaisesRegex(ConnectionError, "commit outage"):
            await consumer.consume_batch(block_ms=0)
        self.assertEqual(len(effects.effects), 1)
        # Simulate lease expiry after the crashed worker; the replacement worker
        # reclaims the event and the downstream idempotency key suppresses a repeat.
        receipts.active.discard(("tenant-a", "worker-a", row[1]["event_id"]))
        result = await consumer.consume_batch(block_ms=0)
        self.assertEqual(result["processed"], 1)
        self.assertEqual(len(effects.effects), 1)
        self.assertEqual(redis.acked, [row[0]])

    async def fail_if_called(self, _event):
        self.fail("handler must not run when receipt storage is unavailable")


if __name__ == "__main__":
    unittest.main()
