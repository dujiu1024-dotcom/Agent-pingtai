from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.observability.alert_notifications import AlertNotificationDispatcher


class FakeStore:
    def __init__(self):
        self.rows = [{
            "event_id": "event-1", "rule_id": "queue-lag",
            "metric_name": "queue_lag", "observed_value": 10.0,
            "threshold": 5.0, "severity": "critical",
            "fired_at": datetime(2026, 11, 16, tzinfo=timezone.utc),
            "description": "queue is delayed",
        }]
        self.marked = []

    async def claim_pending_deliveries(self, *, limit=100, event_id=None):
        return self.rows[:limit]

    async def mark_delivery(self, event_id, *, success, now, error=None):
        self.marked.append((event_id, success, error))


class FakeProvider:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent = []

    async def send(self, event):
        if self.fail:
            raise RuntimeError("sensitive endpoint response")
        self.sent.append(event)


class AlertNotificationDispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_is_persisted(self):
        store, provider = FakeStore(), FakeProvider()
        dispatcher = AlertNotificationDispatcher(store=store, provider=provider)
        self.assertEqual(await dispatcher.retry_pending(), {
            "claimed": 1, "sent": 1, "failed": 0,
        })
        self.assertEqual(store.marked[0][1], True)
        self.assertEqual(len(provider.sent), 1)

    async def test_provider_failure_is_redacted_before_persistence(self):
        store = FakeStore()
        dispatcher = AlertNotificationDispatcher(store=store, provider=FakeProvider(fail=True))
        self.assertEqual(await dispatcher.retry_pending(), {
            "claimed": 1, "sent": 0, "failed": 1,
        })
        self.assertEqual(store.marked[0][2], "RuntimeError")

    async def test_bool_limit_is_rejected(self):
        dispatcher = AlertNotificationDispatcher(store=FakeStore(), provider=FakeProvider())
        with self.assertRaises(ValueError):
            await dispatcher.retry_pending(limit=True)
