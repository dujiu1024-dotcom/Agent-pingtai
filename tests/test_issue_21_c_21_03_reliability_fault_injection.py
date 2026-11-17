"""Deterministic fault-injection regressions for Issue #21 C-21-03.

No Redis, PostgreSQL, model provider, production server, or external resource is
started. Failures are injected at the existing transport/store boundaries.
"""
import asyncio
import unittest
from uuid import uuid4

from agent_platform.events.consumer import RedisStreamsConsumer
from agent_platform.events.receipts import EventReceiptClaim
from agent_platform.runtime.lifecycle import (
    InMemoryRunStateStore,
    RunLifecycleManager,
    RunStatus,
)


class _DurableReceipts:
    """Small durable-across-worker-instances receipt model for replay tests."""

    def __init__(self):
        self.rows = {}

    async def claim(self, *, tenant_id, consumer_name, event_id):
        key = (tenant_id, consumer_name, event_id)
        row = self.rows.get(key)
        if row and row["status"] == "completed":
            return None
        if row and row["status"] == "processing":
            return None
        attempts = row["attempts"] + 1 if row else 1
        token = f"lease-{attempts}"
        self.rows[key] = {"status": "processing", "attempts": attempts, "token": token}
        return EventReceiptClaim(tenant_id, consumer_name, event_id, token, attempts)

    async def is_completed(self, *, tenant_id, consumer_name, event_id):
        row = self.rows.get((tenant_id, consumer_name, event_id))
        return bool(row and row["status"] == "completed")

    async def complete(self, claim):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        row = self.rows.get(key)
        if not row or row["token"] != claim.lease_token:
            return False
        row["status"] = "completed"
        return True

    async def fail(self, claim, error):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        row = self.rows[key]
        if row["token"] != claim.lease_token:
            return False
        row.update(status="retryable", error=error)
        return True

    async def dead_letter(self, claim, error):
        key = (claim.tenant_id, claim.consumer_name, claim.event_id)
        row = self.rows[key]
        if row["token"] != claim.lease_token:
            return False
        row.update(status="dead_lettered", error=error)
        return True


class _Stream:
    def __init__(self, messages):
        self.pending = list(messages)
        self.fail_reads = 0
        self.fail_dlq = 0
        self.acked = []
        self.dead_letters = []

    async def claim_pending(self, **_kwargs):
        if self.fail_reads:
            self.fail_reads -= 1
            raise ConnectionError("redis unavailable")
        return list(self.pending)

    async def read_group(self, **_kwargs):
        return []

    async def acknowledge(self, *, message_id, **_kwargs):
        self.acked.append(message_id)
        self.pending = [item for item in self.pending if item[0] != message_id]
        return 1

    async def dead_letter(self, *, message_id, fields, reason, **_kwargs):
        if self.fail_dlq:
            self.fail_dlq -= 1
            raise ConnectionError("redis dlq unavailable")
        self.dead_letters.append((message_id, dict(fields), reason))
        await self.acknowledge(message_id=message_id)
        return "dlq-1"

    async def pending_summary(self, **_kwargs):
        return {"pending": len(self.pending)}


class _DatabaseReceipts(_DurableReceipts):
    def __init__(self):
        super().__init__()
        self.fail_claims = 0

    async def claim(self, **kwargs):
        if self.fail_claims:
            self.fail_claims -= 1
            raise ConnectionError("postgres unavailable")
        return await super().claim(**kwargs)


class ReliabilityFaultInjectionTests(unittest.IsolatedAsyncioTestCase):
    def make_consumer(self, stream, receipts, handler, *, max_attempts=3):
        return RedisStreamsConsumer(
            transport=stream,
            receipts=receipts,
            tenant_id="tenant-a",
            topic="jobs",
            group="workers",
            consumer_name="worker-a",
            handler=handler,
            max_attempts=max_attempts,
        )

    async def test_worker_restart_recovers_interrupted_run_from_checkpoint(self):
        store = InMemoryRunStateStore()
        await store.create("tenant-a", "run-restart")
        lifecycle_before_restart = RunLifecycleManager(store)
        await lifecycle_before_restart.start("tenant-a", "run-restart")
        await lifecycle_before_restart.mark_interrupted(
            "tenant-a", "run-restart",
            checkpoint_ref="checkpoint-durable-17",
            reason="worker process terminated",
        )

        # A new manager models a fresh process using the same durable store.
        lifecycle_after_restart = RunLifecycleManager(store)
        loaded = []
        resumed = []

        async def load_checkpoint(snapshot):
            loaded.append(snapshot.checkpoint_ref)

        async def resume(snapshot):
            resumed.append(snapshot.run_id)
            await lifecycle_after_restart.finish(
                snapshot.tenant_id, snapshot.run_id, RunStatus.SUCCEEDED,
                checkpoint_ref=snapshot.checkpoint_ref,
            )

        recovered = await lifecycle_after_restart.recover(
            checkpoint_loader=load_checkpoint,
            resume=resume,
            worker_id=None,
        )
        self.assertEqual(recovered, ["run-restart"])
        self.assertEqual(loaded, ["checkpoint-durable-17"])
        self.assertEqual(resumed, ["run-restart"])
        self.assertEqual(
            (await store.get("tenant-a", "run-restart")).status,
            RunStatus.SUCCEEDED,
        )

    async def test_model_timeout_transitions_run_to_timed_out_and_cancels_work(self):
        store = InMemoryRunStateStore()
        await store.create("tenant-a", "run-timeout")
        lifecycle = RunLifecycleManager(store)
        await lifecycle.start("tenant-a", "run-timeout")
        cancelled = asyncio.Event()

        async def model_call():
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        with self.assertRaises(asyncio.TimeoutError):
            await lifecycle.run_with_timeout(
                "tenant-a", "run-timeout", model_call(), timeout=0.01,
            )
        self.assertTrue(cancelled.is_set())
        self.assertEqual(
            (await store.get("tenant-a", "run-timeout")).status,
            RunStatus.TIMED_OUT,
        )

    async def test_redis_outage_leaves_message_pending_for_restarted_consumer(self):
        event_id = str(uuid4())
        message = ("10-0", {"event_id": event_id, "payload": '{"task": "resume"}'})
        stream, receipts, handled = _Stream([message]), _DurableReceipts(), []
        stream.fail_reads = 1

        async def handler(event):
            handled.append(event.event_id)

        first_process = self.make_consumer(stream, receipts, handler)
        with self.assertRaisesRegex(ConnectionError, "redis unavailable"):
            await first_process.consume_batch()
        self.assertEqual(stream.acked, [])
        self.assertEqual(handled, [])

        # Recreate the consumer after the transient Redis outage.
        second_process = self.make_consumer(stream, receipts, handler)
        result = await second_process.consume_batch()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(stream.acked, ["10-0"])
        self.assertEqual(handled, [event_id])

    async def test_database_receipt_outage_does_not_ack_and_recovers(self):
        event_id = str(uuid4())
        message = ("11-0", {"event_id": event_id, "payload": "{}"})
        stream, receipts, handled = _Stream([message]), _DatabaseReceipts(), []
        receipts.fail_claims = 1

        async def handler(event):
            handled.append(event.event_id)

        consumer = self.make_consumer(stream, receipts, handler)
        with self.assertRaisesRegex(ConnectionError, "postgres unavailable"):
            await consumer.consume_batch()
        self.assertEqual(stream.acked, [])
        self.assertEqual(handled, [])

        result = await consumer.consume_batch()
        self.assertEqual(result["processed"], 1)
        self.assertEqual(stream.acked, ["11-0"])
        self.assertEqual(handled, [event_id])

    async def test_duplicate_delivery_after_worker_restart_has_one_effect(self):
        event_id = str(uuid4())
        message = ("12-0", {"event_id": event_id, "payload": '{"amount": 1}'})
        stream, receipts, effects = _Stream([message]), _DurableReceipts(), []

        async def handler(event):
            effects.append(event.event_id)

        first_process = self.make_consumer(stream, receipts, handler)
        self.assertEqual((await first_process.consume_batch())["processed"], 1)

        # Simulate Redis redelivery after a restart: receipt storage survives.
        stream.pending.append(message)
        restarted_process = self.make_consumer(stream, receipts, handler)
        result = await restarted_process.consume_batch()
        self.assertEqual(result["duplicates"], 1)
        self.assertEqual(effects, [event_id])
        self.assertEqual(stream.acked, ["12-0", "12-0"])

    async def test_redis_dlq_failure_keeps_poison_message_pending_for_retry(self):
        message = ("13-0", {"event_id": "invalid-event-id", "payload": "not-json"})
        stream, receipts = _Stream([message]), _DurableReceipts()
        stream.fail_dlq = 1

        async def handler(_event):
            self.fail("invalid envelope must not reach handler")

        consumer = self.make_consumer(stream, receipts, handler)
        first = await consumer.consume_batch()
        self.assertEqual(first["failed"], 1)
        self.assertEqual(stream.acked, [])
        self.assertEqual(len(stream.pending), 1)

        second = await consumer.consume_batch()
        self.assertEqual(second["dead_lettered"], 1)
        self.assertEqual(stream.acked, ["13-0"])
        self.assertEqual(len(stream.dead_letters), 1)


if __name__ == "__main__":
    unittest.main()
