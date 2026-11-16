from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.observability.alert_dispatch import AlertDispatchService
from agent_platform.observability.alerts import AlertEvent


class FakeStore:
    def __init__(self):
        self.rows = {}
        self.next_id = 0

    async def record_once(self, event, *, scope_key, dedupe_key):
        key = (scope_key, dedupe_key)
        if key in self.rows:
            return self.rows[key]["event_id"], False
        self.next_id += 1
        event_id = f"evt-{self.next_id}"
        self.rows[key] = {
            "event_id": event_id, "scope_key": scope_key, "rule_id": event.rule_id,
            "metric_name": event.metric_name, "observed_value": event.observed_value,
            "threshold": event.threshold, "severity": event.severity,
            "fired_at": event.fired_at, "description": event.description,
            "delivery_status": "pending", "delivery_attempts": 0,
        }
        return event_id, True

    async def claim_pending_deliveries(self, *, limit=100, event_id=None):
        rows = []
        for row in self.rows.values():
            if row["delivery_status"] in {"pending", "failed"} and (event_id is None or row["event_id"] == event_id):
                row["delivery_status"] = "sending"
                row["delivery_attempts"] += 1
                rows.append(dict(row))
                if len(rows) >= limit:
                    break
        return rows

    async def mark_delivery(self, event_id, *, success, now, error=None):
        row = next(row for row in self.rows.values() if row["event_id"] == event_id)
        row["delivery_status"] = "sent" if success else "failed"
        row["last_delivery_error"] = error


class AlertDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_persists_before_delivery_and_deduplicates_sent_alerts(self):
        class Notifier:
            def __init__(self):
                self.events = []
            async def notify(self, event):
                self.events.append(event)

        store, notifier = FakeStore(), Notifier()
        service = AlertDispatchService(store, notifier)
        event = AlertEvent("queue", "queue_depth", 120, 100, "critical", datetime.now(timezone.utc), "queue backlog")
        first = await service.dispatch(event, scope_key="tenant-a", dedupe_key="queue:1")
        second = await service.dispatch(event, scope_key="tenant-a", dedupe_key="queue:1")
        self.assertEqual(first, second)
        self.assertEqual(len(notifier.events), 1)
        row = next(iter(store.rows.values()))
        self.assertEqual(row["delivery_status"], "sent")
        self.assertEqual(row["delivery_attempts"], 1)

    async def test_failed_notification_is_redacted_and_retryable(self):
        class FlakyNotifier:
            def __init__(self):
                self.calls = 0
            def notify(self, event):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("webhook api_key=super-secret-token")

        store, notifier = FakeStore(), FlakyNotifier()
        service = AlertDispatchService(store, notifier)
        event = AlertEvent("worker", "worker_crashes", 3, 1, "warning", datetime.now(timezone.utc), "worker failures")
        await service.dispatch(event, scope_key="tenant-a", dedupe_key="worker:1")
        row = next(iter(store.rows.values()))
        self.assertEqual(row["delivery_status"], "failed")
        self.assertNotIn("super-secret-token", row["last_delivery_error"])
        self.assertEqual(await service.retry_pending(), {"claimed": 1, "sent": 1, "failed": 0})
        self.assertEqual(row["delivery_status"], "sent")
        self.assertEqual(notifier.calls, 2)


if __name__ == "__main__":
    unittest.main()
