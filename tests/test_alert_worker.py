from __future__ import annotations

import asyncio
import unittest

from agent_platform.observability.alert_worker import AlertDispatchWorker


class FakeDispatcher:
    def __init__(self, stop_event=None):
        self.calls = []
        self.stop_event = stop_event
        self.fail_once = False

    async def retry_pending(self, *, limit=100):
        self.calls.append(limit)
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("provider token=must-not-be-retained")
        if self.stop_event is not None:
            self.stop_event.set()
        return {"claimed": 3, "sent": 2, "failed": 1}


class AlertDispatchWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_runs_dispatch_cycle_and_stops_gracefully(self):
        stop = asyncio.Event()
        dispatcher = FakeDispatcher(stop)
        worker = AlertDispatchWorker(
            dispatcher, poll_interval_seconds=0.1, batch_size=25,
        )
        await worker.run_forever(stop_event=stop)
        self.assertEqual(dispatcher.calls, [25])
        self.assertEqual(worker.last_result, {"claimed": 3, "sent": 2, "failed": 1})
        self.assertEqual(worker.cycles, 1)
        self.assertIsNone(worker.last_error_type)

    async def test_worker_survives_failed_cycle_without_retaining_error_text(self):
        stop = asyncio.Event()
        dispatcher = FakeDispatcher(stop)
        dispatcher.fail_once = True
        worker = AlertDispatchWorker(
            dispatcher, poll_interval_seconds=0.1, batch_size=10,
        )
        await worker.run_forever(stop_event=stop)
        self.assertEqual(dispatcher.calls, [10, 10])
        self.assertEqual(worker.cycles, 2)
        self.assertEqual(worker.last_result, {"claimed": 3, "sent": 2, "failed": 1})
        self.assertIsNone(worker.last_error_type)

    async def test_worker_stop_method_and_configuration_validation(self):
        dispatcher = FakeDispatcher()
        worker = AlertDispatchWorker(dispatcher)
        worker.stop()
        await worker.run_forever()
        self.assertEqual(dispatcher.calls, [])
        for kwargs in (
            {"poll_interval_seconds": 0},
            {"poll_interval_seconds": True},
            {"batch_size": 0},
            {"batch_size": True},
            {"batch_size": 1001},
        ):
            with self.assertRaises(ValueError):
                AlertDispatchWorker(dispatcher, **kwargs)


if __name__ == "__main__":
    unittest.main()
