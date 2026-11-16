from __future__ import annotations

import asyncio
import unittest

from datetime import datetime, timedelta, timezone

from agent_platform.observability.alert_worker import (
    AlertDispatchWorker, AlertRuleEvaluationWorker,
)
from agent_platform.observability.alerts import AlertManager, AlertRule


class FakeDispatcher:
    def __init__(self, stop_event=None, started_event=None):
        self.calls = []
        self.stop_event = stop_event
        self.started_event = started_event
        self.fail_once = False

    async def retry_pending(self, *, limit=100):
        self.calls.append(limit)
        if self.started_event is not None:
            self.started_event.set()
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("provider token=must-not-be-retained")
        if self.stop_event is not None:
            self.stop_event.set()
        return {"claimed": 3, "sent": 2, "failed": 1}


class FakeNotifier:
    def notify(self, event):
        raise AssertionError("durable worker must persist through the outbox, not notify inline")


class FakeAlertEventStore:
    def __init__(self):
        self.events = {}
        self.fail_once = False

    async def record_once(self, event, *, scope_key, dedupe_key):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("database password=must-not-be-retained")
        key = (scope_key, dedupe_key)
        if key in self.events:
            return self.events[key], False
        event_id = f"event-{len(self.events) + 1}"
        self.events[key] = event_id
        return event_id, True


class AlertRuleEvaluationWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_persists_events_before_advancing_cooldown(self):
        now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        manager = AlertManager(
            (AlertRule("cpu-high", "cpu", 0.8, cooldown=timedelta(minutes=5)),),
            FakeNotifier(), clock=lambda: now,
        )
        store = FakeAlertEventStore()
        worker = AlertRuleEvaluationWorker(
            manager=manager, metrics_provider=lambda: {"cpu": 0.95},
            event_store=store, scope_key="tenant-a",
        )
        store.fail_once = True
        with self.assertRaisesRegex(RuntimeError, "database"):
            await worker.run_once()
        self.assertEqual(manager.evaluate_candidates({"cpu": 0.95})[0].rule_id, "cpu-high")
        result = await worker.run_once()
        self.assertEqual(result, {"observed": 1, "fired": 1, "created": 1, "deduplicated": 0})
        self.assertEqual(await worker.run_once(), {
            "observed": 1, "fired": 0, "created": 0, "deduplicated": 0,
        })
        self.assertEqual(len(store.events), 1)

    async def test_worker_deduplicates_durable_event_before_marking_fired(self):
        now = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        manager = AlertManager(
            (AlertRule("latency", "latency", 2.0, cooldown=timedelta(minutes=5)),),
            FakeNotifier(), clock=lambda: now,
        )
        store = FakeAlertEventStore()
        store.events[("tenant-a", "latency:98000")] = "existing-event"
        worker = AlertRuleEvaluationWorker(
            manager=manager, metrics_provider=lambda: {"latency": 3.0},
            event_store=store, scope_key="tenant-a",
        )
        result = await worker.run_once()
        self.assertEqual(result["deduplicated"], 1)
        self.assertEqual(result["created"], 0)
        self.assertEqual(manager.evaluate_candidates({"latency": 3.0}), ())

    async def test_worker_survives_poll_errors_and_validates_configuration(self):
        stop = asyncio.Event()
        calls = 0
        async def provider():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("provider token=secret")
            stop.set()
            return {}
        manager = AlertManager((), FakeNotifier())
        worker = AlertRuleEvaluationWorker(
            manager=manager, metrics_provider=provider,
            event_store=FakeAlertEventStore(), scope_key="tenant-a",
            poll_interval_seconds=0.1,
        )
        await worker.run_forever(stop_event=stop)
        self.assertEqual(worker.last_error_type, None)
        self.assertEqual(worker.cycles, 2)
        with self.assertRaises(ValueError):
            AlertRuleEvaluationWorker(
                manager=manager, metrics_provider=provider,
                event_store=FakeAlertEventStore(), scope_key="",
            )


class AlertDispatchWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_runs_dispatch_cycle_and_stops_gracefully(self):
        stop = asyncio.Event()
        dispatcher = FakeDispatcher(stop)
        worker = AlertDispatchWorker(dispatcher, poll_interval_seconds=0.1, batch_size=25)
        await worker.run_forever(stop_event=stop)
        self.assertEqual(dispatcher.calls, [25])
        self.assertEqual(worker.last_result, {"claimed": 3, "sent": 2, "failed": 1})
        self.assertEqual(worker.cycles, 1)
        self.assertIsNone(worker.last_error_type)

    async def test_worker_survives_failed_cycle_without_retaining_error_text(self):
        stop = asyncio.Event()
        dispatcher = FakeDispatcher(stop)
        dispatcher.fail_once = True
        worker = AlertDispatchWorker(dispatcher, poll_interval_seconds=0.1, batch_size=10)
        await worker.run_forever(stop_event=stop)
        self.assertEqual(dispatcher.calls, [10, 10])
        self.assertEqual(worker.cycles, 2)
        self.assertEqual(worker.last_result, {"claimed": 3, "sent": 2, "failed": 1})
        self.assertIsNone(worker.last_error_type)

    async def test_start_is_idempotent_and_stop_waits_for_task(self):
        started = asyncio.Event()
        dispatcher = FakeDispatcher(started_event=started)
        worker = AlertDispatchWorker(dispatcher, poll_interval_seconds=0.1)
        await worker.start()
        first_task = worker._task
        await worker.start()
        self.assertIs(worker._task, first_task)
        self.assertTrue(worker.running)
        await asyncio.wait_for(started.wait(), timeout=1)
        await worker.stop()
        self.assertFalse(worker.running)
        self.assertEqual(dispatcher.calls, [100])

    async def test_stop_before_start_prevents_future_cycles_until_restart(self):
        dispatcher = FakeDispatcher()
        worker = AlertDispatchWorker(dispatcher)
        await worker.stop()
        await worker.start()
        await asyncio.sleep(0)
        await worker.stop()
        self.assertEqual(dispatcher.calls, [100])

    async def test_invalid_dispatcher_counters_are_rejected(self):
        class InvalidDispatcher:
            async def retry_pending(self, *, limit=100):
                return {"claimed": True, "sent": 0, "failed": 0}
        worker = AlertDispatchWorker(InvalidDispatcher())
        with self.assertRaisesRegex(ValueError, "integers"):
            await worker.run_once()

    async def test_worker_stop_method_and_configuration_validation(self):
        dispatcher = FakeDispatcher()
        worker = AlertDispatchWorker(dispatcher)
        worker._stop.set()
        await worker.run_forever()
        self.assertEqual(dispatcher.calls, [])
        for kwargs in (
            {"poll_interval_seconds": 0},
            {"poll_interval_seconds": True},
            {"poll_interval_seconds": "1"},
            {"batch_size": 0},
            {"batch_size": True},
            {"batch_size": 1.5},
            {"batch_size": 1001},
        ):
            with self.assertRaises(ValueError):
                AlertDispatchWorker(dispatcher, **kwargs)


if __name__ == "__main__":
    unittest.main()
