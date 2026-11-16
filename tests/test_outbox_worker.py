from __future__ import annotations

import asyncio
import unittest

from agent_platform.events.outbox_worker import OutboxRelayWorker


class FakeRelay:
    def __init__(self, fail_cycles=0):
        self.fail_cycles = fail_cycles
        self.calls = []

    async def publish_pending(self, *, limit):
        self.calls.append(limit)
        if self.fail_cycles:
            self.fail_cycles -= 1
            raise RuntimeError("private transport details")


class OutboxRelayWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_idempotent_and_stop_waits_for_worker(self):
        relay = FakeRelay()
        worker = OutboxRelayWorker(
            relay=relay, interval_seconds=0.1, batch_limit=7,
        )
        await worker.start()
        await worker.start()
        await asyncio.sleep(0.02)
        self.assertTrue(worker.running)
        self.assertTrue(relay.calls)
        self.assertTrue(all(limit == 7 for limit in relay.calls))
        await worker.stop()
        self.assertFalse(worker.running)
        calls_at_stop = len(relay.calls)
        await asyncio.sleep(0.02)
        self.assertEqual(len(relay.calls), calls_at_stop)

    async def test_transient_failure_does_not_kill_worker(self):
        relay = FakeRelay(fail_cycles=1)
        worker = OutboxRelayWorker(relay=relay, interval_seconds=0.1)
        await worker.start()
        await asyncio.sleep(0.15)
        await worker.stop()
        self.assertGreaterEqual(len(relay.calls), 2)

    async def test_redis_outage_replays_durable_outbox_after_transport_recovers(self):
        from agent_platform.events.outbox import OutboxRecord, OutboxRelay

        class DurableStore:
            def __init__(self):
                self.record = OutboxRecord(
                    tenant_id="tenant-a", outbox_id=9, event_id="event-replay",
                    topic="platform.run.completed", payload={"run_id": "run-9"},
                    lease_token="00000000-0000-0000-0000-000000000009",
                )
                self.published = False
                self.failures = 0

            async def claim_batch(self, *, limit):
                return [] if self.published else [self.record][:limit]

            async def mark_published(self, record):
                self.published = True
                return True

            async def mark_failed(self, record, error):
                self.failures += 1

        class RecoveringRedis:
            def __init__(self):
                self.calls = 0
                self.events = []

            async def publish(self, **event):
                self.calls += 1
                if self.calls == 1:
                    raise ConnectionError("redis unavailable")
                self.events.append(event)
                return "1-0"

        store, redis = DurableStore(), RecoveringRedis()
        relay = OutboxRelay(store=store, transport=redis)
        worker = OutboxRelayWorker(relay=relay, interval_seconds=0.1)
        await worker.start()
        for _ in range(20):
            if store.published:
                break
            await asyncio.sleep(0.02)
        await worker.stop()
        self.assertEqual(store.failures, 1)
        self.assertTrue(store.published)
        self.assertEqual(len(redis.events), 1)
        self.assertEqual(redis.events[0]["event_id"], "event-replay")

    async def test_rejects_unsafe_poll_configuration(self):
        with self.assertRaises(ValueError):
            OutboxRelayWorker(relay=FakeRelay(), interval_seconds=0)
        with self.assertRaises(ValueError):
            OutboxRelayWorker(relay=FakeRelay(), batch_limit=0)


if __name__ == "__main__":
    unittest.main()
