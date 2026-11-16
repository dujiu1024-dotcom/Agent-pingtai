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

    async def test_rejects_unsafe_poll_configuration(self):
        with self.assertRaises(ValueError):
            OutboxRelayWorker(relay=FakeRelay(), interval_seconds=0)
        with self.assertRaises(ValueError):
            OutboxRelayWorker(relay=FakeRelay(), batch_limit=0)


if __name__ == "__main__":
    unittest.main()
