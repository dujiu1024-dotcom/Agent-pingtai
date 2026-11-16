from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.observability.alert_notifications import (
    AlertNotificationDispatcher, WebhookAlertNotificationProvider,
)
from agent_platform.observability.alerts import AlertEvent


class FakeStore:
    def __init__(self):
        self.rows = [{
            "event_id": "event-1", "scope_key": "tenant-a",
            "delivery_attempts": 1, "rule_id": "queue-lag",
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


class FakeAuditSink:
    def __init__(self, fail=False, fail_on_call=None):
        self.events = []
        self.fail = fail
        self.fail_on_call = fail_on_call
        self.calls = 0

    async def record(self, **event):
        self.calls += 1
        if self.fail or self.calls == self.fail_on_call:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


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
        store, provider, audit = FakeStore(), FakeProvider(), FakeAuditSink()
        dispatcher = AlertNotificationDispatcher(
            store=store, provider=provider, audit_sink=audit,
        )
        self.assertEqual(await dispatcher.retry_pending(), {
            "claimed": 1, "sent": 1, "failed": 0,
        })
        self.assertEqual(store.marked[0][1], True)
        self.assertEqual(len(provider.sent), 1)
        self.assertEqual([event["action"] for event in audit.events], [
            "alert.notification.send.attempt", "alert.notification.send",
        ])
        self.assertEqual(audit.events[0]["actor_id"], "system:alert-notification-dispatcher")

    async def test_provider_failure_is_redacted_before_persistence(self):
        store, audit = FakeStore(), FakeAuditSink()
        dispatcher = AlertNotificationDispatcher(
            store=store, provider=FakeProvider(fail=True), audit_sink=audit,
        )
        self.assertEqual(await dispatcher.retry_pending(), {
            "claimed": 1, "sent": 0, "failed": 1,
        })
        self.assertEqual(store.marked[0][2], "RuntimeError")
        self.assertEqual(audit.events[-1]["outcome"], "failed")


    async def test_audit_unavailable_prevents_alert_provider_side_effect(self):
        store, provider = FakeStore(), FakeProvider()
        dispatcher = AlertNotificationDispatcher(
            store=store, provider=provider, audit_sink=FakeAuditSink(fail=True),
        )
        self.assertEqual(await dispatcher.retry_pending(), {
            "claimed": 1, "sent": 0, "failed": 1,
        })
        self.assertEqual(provider.sent, [])
        self.assertEqual(store.marked[0][1], False)
        self.assertEqual(store.marked[0][2], "AuditUnavailable")

    async def test_post_send_audit_failure_does_not_requeue_sent_alert(self):
        store, provider = FakeStore(), FakeProvider()
        dispatcher = AlertNotificationDispatcher(
            store=store, provider=provider, audit_sink=FakeAuditSink(fail_on_call=2),
        )
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await dispatcher.retry_pending()
        self.assertEqual(len(provider.sent), 1)
        self.assertEqual(store.marked[0][1], True)

    async def test_bool_limit_is_rejected(self):
        dispatcher = AlertNotificationDispatcher(
            store=FakeStore(), provider=FakeProvider(), audit_sink=FakeAuditSink(),
        )
        with self.assertRaises(ValueError):
            await dispatcher.retry_pending(limit=True)



class FakeWebhookResponse:
    def __init__(self, status_code):
        self.status_code = status_code


class FakeWebhookClient:
    def __init__(self, status_code=204):
        self.status_code = status_code
        self.calls = []

    async def post(self, url, *, json, headers, timeout):
        self.calls.append((url, json, headers, timeout))
        return FakeWebhookResponse(self.status_code)


class WebhookAlertNotificationProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.event = AlertEvent(
            rule_id="queue-lag",
            metric_name="queue_lag",
            observed_value=10.0,
            threshold=5.0,
            severity="critical",
            fired_at=datetime(2026, 11, 16, tzinfo=timezone.utc),
            description="queue delay detected",
        )

    async def test_sends_redacted_payload_to_allowlisted_https_endpoint(self):
        client = FakeWebhookClient()
        provider = WebhookAlertNotificationProvider(
            client=client,
            endpoint="https://alerts.example.com/hooks/platform",
            allowed_hosts=("alerts.example.com",),
            authorization_header="Bearer injected-secret",
        )
        await provider.send(self.event)
        url, payload, headers, timeout = client.calls[0]
        self.assertEqual(url, "https://alerts.example.com/hooks/platform")
        self.assertEqual(payload["rule_id"], "queue-lag")
        self.assertEqual(payload["severity"], "critical")
        self.assertEqual(headers["authorization"], "Bearer injected-secret")
        self.assertEqual(timeout, 5.0)

    async def test_rejects_non_https_and_non_allowlisted_hosts(self):
        client = FakeWebhookClient()
        for endpoint, hosts in (
            ("http://alerts.example.com/hook", ("alerts.example.com",)),
            ("https://other.example.com/hook", ("alerts.example.com",)),
            ("https://user:pass@alerts.example.com/hook", ("alerts.example.com",)),
            ("https://alerts.example.com:8443/hook", ("alerts.example.com",)),
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValueError):
                    WebhookAlertNotificationProvider(
                        client=client, endpoint=endpoint, allowed_hosts=hosts,
                    )
        self.assertEqual(client.calls, [])

    async def test_provider_failure_does_not_expose_response_body(self):
        client = FakeWebhookClient(status_code=503)
        provider = WebhookAlertNotificationProvider(
            client=client,
            endpoint="https://alerts.example.com/hook",
            allowed_hosts=("alerts.example.com",),
        )
        with self.assertRaisesRegex(RuntimeError, "non-success status"):
            await provider.send(self.event)
