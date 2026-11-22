from __future__ import annotations

import asyncio
import unittest

from agent_platform.storage.production_composition import ManagedFileProcessingWorker


class ManagedFileProcessingWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_idempotent_and_stop_waits_for_worker(self):
        class Worker:
            def __init__(self):
                self.calls = 0
                self.entered = asyncio.Event()

            async def run_once(self):
                self.calls += 1
                self.entered.set()
                return False

        worker = Worker()
        managed = ManagedFileProcessingWorker(worker=worker, poll_interval_seconds=0.01)
        await managed.start()
        await worker.entered.wait()
        await managed.start()
        self.assertTrue(managed.running)
        await managed.stop()
        self.assertFalse(managed.running)
        self.assertGreaterEqual(worker.calls, 1)

    async def test_transient_worker_error_does_not_kill_lifecycle(self):
        class Worker:
            def __init__(self):
                self.calls = 0
                self.recovered = asyncio.Event()

            async def run_once(self):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("temporary database outage")
                self.recovered.set()
                return False

        worker = Worker()
        managed = ManagedFileProcessingWorker(worker=worker, poll_interval_seconds=0.01)
        await managed.start()
        await asyncio.wait_for(worker.recovered.wait(), timeout=1)
        self.assertTrue(managed.running)
        await managed.stop()

    async def test_invalid_worker_or_poll_interval_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "poll interval"):
            ManagedFileProcessingWorker(worker=object(), poll_interval_seconds=0)
        with self.assertRaisesRegex(TypeError, "run_once"):
            ManagedFileProcessingWorker(worker=object())


if __name__ == "__main__":
    unittest.main()
