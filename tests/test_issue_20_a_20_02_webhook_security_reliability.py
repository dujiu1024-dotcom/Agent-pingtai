"""A-20-02 security and reliability acceptance tests for webhooks."""
from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.events.webhook_operations import PostgresWebhookOperationsStore
from agent_platform.events.webhook_replay import WebhookDeadLetterReplayService
from agent_platform.events.webhooks import (
    WebhookAttempt,
    WebhookDispatcher,
    WebhookEgressPolicy,
    WebhookPayload,
    WebhookSubscription,
    signature,
    verify_signature,
)

SECRET = b"webhook-security-test-secret-32b"


class SignatureSecurityTests(unittest.TestCase):
    def test_valid_signature_is_accepted_within_inclusive_timestamp_window(self):
        body = b'{"id":"evt-1","type":"run.completed"}'
        issued_at = 1_800_000_000
        signed = signature(SECRET, str(issued_at), body)

        self.assertTrue(verify_signature(
            SECRET, str(issued_at), body, signed,
            now=issued_at + 300, tolerance_seconds=300,
        ))

    def test_expired_and_future_signatures_are_rejected(self):
        body = b'{"id":"evt-2"}'
        issued_at = 1_800_000_000
        signed = signature(SECRET, str(issued_at), body)

        self.assertFalse(verify_signature(
            SECRET, str(issued_at), body, signed,
            now=issued_at + 301, tolerance_seconds=300,
        ))
        self.assertFalse(verify_signature(
            SECRET, str(issued_at), body, signed,
            now=issued_at - 301, tolerance_seconds=300,
        ))

    def test_body_signature_and_timestamp_tampering_are_rejected(self):
        body = b'{"id":"evt-3","amount":10}'
        issued_at = 1_800_000_000
        timestamp = str(issued_at)
        signed = signature(SECRET, timestamp, body)

        self.assertFalse(verify_signature(
            SECRET, timestamp, body + b',"amount":100', signed, now=issued_at,
        ))
        self.assertFalse(verify_signature(
            SECRET, timestamp, body, "sha256=" + "0" * 64, now=issued_at,
        ))
        self.assertFalse(verify_signature(
            SECRET, str(issued_at + 1), body, signed, now=issued_at,
        ))

    def test_weak_secrets_and_malformed_signature_inputs_fail_closed(self):
        body = b"payload"
        timestamp = "1800000000"
        signed = signature(SECRET, timestamp, body)

        self.assertFalse(verify_signature(b"short", timestamp, body, signed, now=1_800_000_000))
        for supplied in ("", "sha256=xyz", "sha256=" + "A" * 64, "sha256=" + "0" * 63):
            with self.subTest(supplied=supplied):
                self.assertFalse(verify_signature(
                    SECRET, timestamp, body, supplied, now=1_800_000_000,
                ))


class _MultiTenantDeliveryStore:
    def __init__(self):
        self.records = []
        self.retries = []
        self.dead_letters = []

    async def claim(self, *, limit):
        return self.records[:limit]

    async def complete(self, tenant_id, delivery_id, *, response_code, lease_token):
        return True

    async def retry(self, tenant_id, delivery_id, *, next_attempt_at, response_code,
                    lease_token, last_error_code):
        self.retries.append({
            "tenant_id": tenant_id, "delivery_id": delivery_id,
            "response_code": response_code, "lease_token": lease_token,
            "last_error_code": last_error_code, "next_attempt_at": next_attempt_at,
        })
        return True

    async def dead_letter(self, tenant_id, delivery_id, *, response_code, lease_token,
                          last_error_code):
        self.dead_letters.append({
            "tenant_id": tenant_id, "delivery_id": delivery_id,
            "response_code": response_code, "lease_token": lease_token,
            "last_error_code": last_error_code,
        })
        return True


class _AlwaysFailTransport:
    async def post_pinned(self, url, *, resolved_addresses, body, headers, timeout_seconds):
        return 503


class _Audit:
    def __init__(self):
        self.events = []

    async def record(self, **event):
        self.events.append(event)


class DeliveryRetryAndTenantIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_and_dead_letter_transitions_keep_each_tenant_scope(self):
        store = _MultiTenantDeliveryStore()
        audit = _Audit()
        for tenant_id, delivery_id, attempt_number in (
            ("tenant-a", "delivery-a", 2),
            ("tenant-b", "delivery-b", 3),
        ):
            subscription = WebhookSubscription(
                f"sub-{tenant_id}", tenant_id, "https://hooks.example.test/events",
                f"secret://{tenant_id}/webhook", ("run.completed",),
            )
            payload = WebhookPayload(
                tenant_id, f"event-{tenant_id}", "run.completed", {"tenant": tenant_id},
            )
            attempt = WebhookAttempt(
                tenant_id, delivery_id, payload.event_id, attempt_number, "leased", None,
                lease_token=f"lease-{tenant_id}",
            )
            store.records.append((subscription, payload, attempt))

        dispatcher = WebhookDispatcher(
            store=store,
            transport=_AlwaysFailTransport(),
            resolve_secret=lambda _ref: SECRET,
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
            audit_sink=audit,
            max_attempts=3,
            clock=lambda: datetime(2026, 11, 18, tzinfo=timezone.utc),
        )
        result = await dispatcher.dispatch(limit=10)

        self.assertEqual(result["retrying"], 1)
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(
            [(item["tenant_id"], item["delivery_id"], item["lease_token"])
             for item in store.retries],
            [("tenant-a", "delivery-a", "lease-tenant-a")],
        )
        self.assertEqual(
            [(item["tenant_id"], item["delivery_id"], item["lease_token"])
             for item in store.dead_letters],
            [("tenant-b", "delivery-b", "lease-tenant-b")],
        )
        self.assertEqual(
            {event["tenant_id"] for event in audit.events},
            {"tenant-a", "tenant-b"},
        )


class _ReplayStore:
    def __init__(self):
        self.calls = []

    async def replay_dead_letter(self, tenant_id, delivery_id):
        self.calls.append((tenant_id, delivery_id))
        return True


class ReplayAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_replay_authorization_is_bound_to_tenant_and_actor(self):
        store, audit = _ReplayStore(), _Audit()
        service = WebhookDeadLetterReplayService(
            store=store,
            audit=audit,
            authorize=lambda tenant_id, actor_id: (
                tenant_id == "tenant-a" and actor_id == "operator-a"
            ),
        )

        self.assertTrue(await service.replay(
            tenant_id="tenant-a", actor_id="operator-a",
            delivery_id="delivery-a", reason="recover endpoint",
        ))
        with self.assertRaises(PermissionError):
            await service.replay(
                tenant_id="tenant-b", actor_id="operator-a",
                delivery_id="delivery-a", reason="cross-tenant attempt",
            )

        self.assertEqual(store.calls, [("tenant-a", "delivery-a")])
        self.assertEqual(audit.events[-1]["outcome"], "denied")
        self.assertEqual(audit.events[-1]["tenant_id"], "tenant-b")


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _RecordingConnection:
    def __init__(self):
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def transaction(self):
        return _Transaction()

    async def execute(self, query, params):
        self.statements.append((query, params))
        return self

    async def fetchall(self):
        return []

    async def fetchone(self):
        return None


class PostgresTenantPredicateTests(unittest.IsolatedAsyncioTestCase):
    async def test_dead_letter_listing_and_replay_are_scoped_by_tenant_in_sql(self):
        connection = _RecordingConnection()

        async def connect():
            return connection

        store = PostgresWebhookOperationsStore(connect)
        self.assertEqual(await store.list_dead_letters("tenant-a", limit=25), [])
        self.assertFalse(await store.replay_dead_letter(
            "tenant-b", "00000000-0000-0000-0000-000000000001",
        ))

        list_query, list_params = connection.statements[0]
        replay_query, replay_params = connection.statements[1]
        self.assertIn("WHERE tenant_id=%s AND status='dead_lettered'", list_query)
        self.assertEqual(list_params, ("tenant-a", 25))
        self.assertIn("WHERE d.tenant_id=%s AND d.delivery_id=%s", replay_query)
        self.assertIn("s.tenant_id=d.tenant_id", replay_query)
        self.assertEqual(replay_params[0], "tenant-b")


if __name__ == "__main__":
    unittest.main()
