from __future__ import annotations

import json
import time
import unittest
from unittest.mock import MagicMock
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime as format_http_date
from urllib.request import Request

from agent_platform.events.webhooks import (
    WebhookAttempt, WebhookDispatcher, WebhookEgressPolicy, WebhookPayload,
    PostgresWebhookStore, WebhookSubscription, signature, verify_signature,
)
from agent_platform.sdk.client import ApiResponse, PlatformApiError, PlatformClient
from agent_platform.extensions.manifest import ExtensionManifest, validate_platform_compatibility
from agent_platform.i18n.context import format_datetime, format_number, locale_context


SECRET = b"s" * 32


class FakeWebhookStore:
    def __init__(self, attempt=1):
        self.subscription = WebhookSubscription(
            "sub-1", "tenant-a", "https://hooks.example.test/agent",
            "secret://tenant-a/webhook-1", ("run.completed",),
        )
        self.payload = WebhookPayload(
            "tenant-a", "event-1", "run.completed", {"run_id": "run-1"},
        )
        self.attempt = WebhookAttempt(
            "tenant-a", "delivery-1", "event-1", attempt, "leased", None,
            lease_token="00000000-0000-0000-0000-0000000000aa",
        )
        self.completed = []
        self.retried = []
        self.dead = []
        self.complete_result = True
        self.retry_result = True
        self.dead_result = True

    async def claim(self, *, limit):
        return [(self.subscription, self.payload, self.attempt)]

    async def complete(self, tenant_id, delivery_id, *, response_code, lease_token):
        self.completed.append((tenant_id, delivery_id, response_code, lease_token))
        return self.complete_result

    async def retry(self, tenant_id, delivery_id, *, next_attempt_at, response_code,
                    lease_token, last_error_code):
        self.retried.append((tenant_id, delivery_id, next_attempt_at, response_code,
                             lease_token, last_error_code))
        return self.retry_result

    async def dead_letter(self, tenant_id, delivery_id, *, response_code, lease_token,
                          last_error_code):
        self.dead.append((tenant_id, delivery_id, response_code, lease_token, last_error_code))
        return self.dead_result


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


class FakeWebhookTransport:
    def __init__(self, code=204):
        self.code = code
        self.requests = []

    async def post_pinned(self, url, *, resolved_addresses, body, headers, timeout_seconds):
        self.requests.append((url, body, headers, timeout_seconds, resolved_addresses))
        return self.code


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    def test_signature_verification_is_constant_contract(self):
        body = b'{"event":"ok"}'
        signed = signature(SECRET, "1794787200", body)
        self.assertTrue(verify_signature(SECRET, "1794787200", body, signed, now=1794787200))
        self.assertFalse(verify_signature(SECRET, "1794787200", body + b"x", signed))
        self.assertFalse(verify_signature(SECRET, "not-a-time", body, signed))

    def test_signature_verification_rejects_stale_future_and_invalid_windows(self):
        body = b'{"event":"ok"}'
        timestamp = 1_794_787_200
        signed = signature(SECRET, str(timestamp), body)
        self.assertTrue(verify_signature(
            SECRET, str(timestamp), body, signed, now=timestamp + 300,
        ))
        self.assertFalse(verify_signature(
            SECRET, str(timestamp), body, signed, now=timestamp + 301,
        ))
        self.assertFalse(verify_signature(
            SECRET, str(timestamp), body, signed, now=timestamp - 301,
        ))
        self.assertFalse(verify_signature(
            SECRET, str(timestamp), body, signed, now=timestamp, tolerance_seconds=0,
        ))
        self.assertFalse(verify_signature(
            SECRET, str(timestamp), body, signed, now=timestamp, tolerance_seconds=901,
        ))
        self.assertFalse(verify_signature(
            SECRET, "١٧٩٤٧٨٧٢٠٠", body, signed, now=timestamp,
        ))

    def test_signature_verification_uses_current_time_by_default(self):
        stale = str(int(time.time()) - 301)
        signed = signature(SECRET, stale, b"payload")
        self.assertFalse(verify_signature(SECRET, stale, b"payload", signed))

    def test_subscription_rejects_non_https_and_local_targets(self):
        for url in ("http://hooks.example.test", "https://localhost/hook",
                    "https://user:pass@hooks.example.test/hook", "https://10.0.0.1/hook",
                    "https://service.internal/hook"):
            with self.assertRaises(ValueError):
                WebhookSubscription("s", "t", url, "secret://ref", ("topic",))


    async def test_egress_policy_rejects_private_and_mixed_dns_answers(self):
        private = WebhookEgressPolicy(
            resolver=lambda _host, _port: ("93.184.216.34", "127.0.0.1"),
        )
        with self.assertRaisesRegex(ValueError, "non-public"):
            await private.resolve_public_addresses("https://hooks.example.test/event")

        direct_private = WebhookEgressPolicy()
        with self.assertRaisesRegex(ValueError, "non-public"):
            await direct_private.resolve_public_addresses("https://127.0.0.1/hook")

    async def test_egress_policy_returns_normalized_public_ip_set(self):
        policy = WebhookEgressPolicy(
            resolver=lambda _host, _port: ("2606:4700:4700::1111", "93.184.216.34",
                                           "93.184.216.34"),
        )
        addresses = await policy.resolve_public_addresses("https://hooks.example.test/event")
        self.assertEqual(addresses, ("2606:4700:4700::1111", "93.184.216.34"))

    async def test_dispatch_signs_and_deduplicates_with_event_id(self):
        store = FakeWebhookStore()
        transport = FakeWebhookTransport()
        audit = FakeAuditSink()
        dispatcher = WebhookDispatcher(
            store=store, transport=transport, resolve_secret=lambda _ref: SECRET,
            audit_sink=audit,
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
            clock=lambda: datetime(2026, 11, 16, tzinfo=timezone.utc),
        )
        result = await dispatcher.dispatch()
        self.assertEqual(result["delivered"], 1)
        _, body, headers, _, resolved = transport.requests[0]
        self.assertEqual(resolved, ("93.184.216.34",))
        self.assertEqual(headers["Idempotency-Key"], "event-1")
        self.assertTrue(verify_signature(
            SECRET, headers["X-Platform-Timestamp"], body,
            headers["X-Platform-Signature"],
            now=int(headers["X-Platform-Timestamp"]),
        ))
        self.assertEqual(json.loads(body)["id"], "event-1")
        self.assertEqual(store.completed[0], ("tenant-a", "delivery-1", 204, "00000000-0000-0000-0000-0000000000aa"))
        self.assertEqual([event["action"] for event in audit.events], [
            "webhook.delivery.attempt", "webhook.delivery",
        ])
        self.assertEqual(audit.events[0]["actor_id"], "system:webhook-dispatcher")
        self.assertEqual(audit.events[1]["outcome"], "succeeded")

    async def test_audit_write_ahead_failure_prevents_webhook_side_effect(self):
        store = FakeWebhookStore()
        transport = FakeWebhookTransport()
        dispatcher = WebhookDispatcher(
            store=store, transport=transport, resolve_secret=lambda _ref: SECRET,
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
            audit_sink=FakeAuditSink(fail=True),
        )
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await dispatcher.dispatch()
        self.assertEqual(transport.requests, [])
        self.assertEqual(store.completed, [])
        self.assertEqual(store.retried, [])
        self.assertEqual(store.dead, [])

    async def test_post_delivery_audit_failure_does_not_schedule_duplicate_delivery(self):
        store = FakeWebhookStore()
        transport = FakeWebhookTransport()
        dispatcher = WebhookDispatcher(
            store=store, transport=transport, resolve_secret=lambda _ref: SECRET,
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
            audit_sink=FakeAuditSink(fail_on_call=2),
        )
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await dispatcher.dispatch()
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(len(store.completed), 1)
        self.assertEqual(store.retried, [])
        self.assertEqual(store.dead, [])

    async def test_dispatch_supports_async_secret_manager_resolver(self):
        store = FakeWebhookStore()
        transport = FakeWebhookTransport()
        audit = FakeAuditSink()

        async def resolve_secret(_ref):
            return SECRET

        dispatcher = WebhookDispatcher(
            store=store,
            transport=transport,
            resolve_secret=resolve_secret,
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
            audit_sink=audit,
            clock=lambda: datetime(2026, 11, 16, tzinfo=timezone.utc),
        )
        result = await dispatcher.dispatch()
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(len(store.completed), 1)
        self.assertEqual([event["action"] for event in audit.events], [
            "webhook.delivery.attempt", "webhook.delivery",
        ])

    async def test_stale_delivery_completion_is_not_reported_as_delivered(self):
        store = FakeWebhookStore()
        store.complete_result = False
        audit = FakeAuditSink()
        dispatcher = WebhookDispatcher(
            store=store, transport=FakeWebhookTransport(code=204),
            resolve_secret=lambda _ref: SECRET,
            audit_sink=audit,
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
        )
        result = await dispatcher.dispatch()
        self.assertEqual(result["delivered"], 0)
        self.assertEqual(result["lease_lost"], 1)
        self.assertEqual(audit.events[-1]["action"], "webhook.delivery.lease_lost")

    async def test_dispatch_retry_persists_lease_fence_and_safe_error_code(self):
        store = FakeWebhookStore(attempt=2)
        transport = FakeWebhookTransport(code=503)
        dispatcher = WebhookDispatcher(
            store=store, transport=transport, resolve_secret=lambda _ref: SECRET,
            audit_sink=FakeAuditSink(),
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
            clock=lambda: datetime(2026, 11, 16, tzinfo=timezone.utc),
        )
        result = await dispatcher.dispatch()
        self.assertEqual(result["retrying"], 1)
        self.assertEqual(len(store.retried), 1)
        self.assertEqual(store.retried[0][3:], (503, "00000000-0000-0000-0000-0000000000aa", "non_success_response"))

    async def test_dispatch_supports_async_secret_manager_resolver(self):
        store = FakeWebhookStore()
        transport = FakeWebhookTransport()

        async def resolve_secret(_ref):
            return SECRET

        dispatcher = WebhookDispatcher(
            store=store, transport=transport, resolve_secret=resolve_secret,
            egress_policy=WebhookEgressPolicy(resolver=lambda host, port: ("93.184.216.34",)),
            clock=lambda: datetime(2026, 11, 16, tzinfo=timezone.utc),
            audit_sink=FakeAuditSink(),
        )
        result = await dispatcher.dispatch()
        self.assertEqual(result["delivered"], 1)
        self.assertEqual(len(store.completed), 1)

    async def test_dispatch_retries_then_dead_letters(self):
        store = FakeWebhookStore(attempt=8)
        transport = FakeWebhookTransport(code=503)
        audit = FakeAuditSink()
        dispatcher = WebhookDispatcher(
            store=store, transport=transport, resolve_secret=lambda _ref: SECRET,
            audit_sink=audit,
            egress_policy=WebhookEgressPolicy(
                resolver=lambda _host, _port: ("93.184.216.34",),
            ),
            max_attempts=8,
        )
        result = await dispatcher.dispatch()
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(len(store.dead), 1)
        self.assertEqual(store.dead[0][3:], ("00000000-0000-0000-0000-0000000000aa", "non_success_response"))
        self.assertEqual(audit.events[-1]["outcome"], "failed")
        self.assertTrue(audit.events[-1]["detail"]["dead_lettered"])


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _RecordingConnection:
    def __init__(self):
        self.query = None
        self.params = None
        self.rowcount = 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def transaction(self):
        return _Transaction()

    async def execute(self, query, params):
        self.query = query
        self.params = params
        return self


class WebhookLeaseFencingTests(unittest.IsolatedAsyncioTestCase):
    async def test_finalization_updates_require_the_current_lease_token(self):
        connection = _RecordingConnection()
        store = PostgresWebhookStore(lambda: _async_value(connection))
        await store.complete(
            "tenant-a", "00000000-0000-0000-0000-000000000001",
            response_code=204, lease_token="00000000-0000-0000-0000-0000000000bb",
        )
        self.assertIn("AND lease_token=%s", connection.query)
        self.assertEqual(str(connection.params[-1]), "00000000-0000-0000-0000-0000000000bb")
        self.assertIn("last_error_code=%s", connection.query)
        self.assertIsNone(connection.params[3])

    async def test_finalization_rejects_missing_lease_token(self):
        store = PostgresWebhookStore(lambda: _async_value(_RecordingConnection()))
        with self.assertRaisesRegex(ValueError, "lease_token is required"):
            await store.complete(
                "tenant-a", "00000000-0000-0000-0000-000000000001",
                response_code=204, lease_token="",
            )


async def _async_value(value):
    return value


class FakeSdkTransport:
    def __init__(self, status=200):
        self.status = status
        self.request = None

    def send(self, request: Request, *, timeout: float):
        self.request = request
        return ApiResponse(self.status, {}, {"ok": True})


class SdkAndExtensionTests(unittest.TestCase):
    def test_sdk_enforces_https_versioned_path_and_idempotency(self):
        transport = FakeSdkTransport()
        client = PlatformClient(
            base_url="https://platform.example.test/", token="token",
            transport=transport,
        )
        self.assertEqual(client.post(
            "/runs", {"task": "demo"}, idempotency_key="request-1",
        ), {"ok": True})
        self.assertEqual(transport.request.full_url,
                         "https://platform.example.test/api/platform/v1/runs")
        self.assertEqual(transport.request.get_header("Idempotency-key"), "request-1")
        self.assertEqual(transport.request.get_header("Authorization"), "Bearer token")
        with self.assertRaises(ValueError):
            client.get("/../admin")
        with self.assertRaises(ValueError):
            PlatformClient(base_url="http://insecure.test", token="x")

    def test_sdk_rejects_unsafe_origins_paths_methods_and_idempotency_keys(self):
        for origin in (
            "https://user:pass@platform.example.test",
            "https://platform.example.test/api",
            "https://platform.example.test?debug=1",
            "http://platform.example.test",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                PlatformClient(base_url=origin, token="token")

        client = PlatformClient(
            base_url="https://platform.example.test", token="token",
            transport=FakeSdkTransport(),
        )
        for path in ("/../admin", "/%2e%2e/admin", "//attacker.test", "/safe#fragment", "/bad\\\\path"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                client.get(path)
        for method in ("TRACE", "CONNECT", ""):
            with self.subTest(method=method), self.assertRaises(ValueError):
                client.request(method, "/health")
        for key in ("", "bad\nkey", "x" * 256):
            with self.subTest(key=key), self.assertRaises(ValueError):
                client.post("/runs", {}, idempotency_key=key)
        with self.assertRaises(ValueError):
            PlatformClient(base_url="https://platform.example.test", token="token", timeout=True)

    def test_sdk_surfaces_api_errors(self):
        client = PlatformClient(
            base_url="https://platform.example.test", token="x",
            transport=FakeSdkTransport(status=403),
        )
        with self.assertRaises(PlatformApiError) as caught:
            client.get("/me")
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(caught.exception.body, {"ok": True})
        self.assertEqual(caught.exception.headers, {})

    def test_tenant_locale_formats_timezone_and_numbers_without_global_locale(self):
        context = locale_context("de-DE", "Europe/Berlin")
        value = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
        self.assertIn("+01:00", format_datetime(value, context))
        self.assertEqual(format_number(12345.67, context), "12.345,67")
        with self.assertRaises(ValueError):
            locale_context("xx-XX", "Mars/Olympus")

    def test_extension_manifest_requires_declared_permissions_and_compatible_api(self):
        manifest = ExtensionManifest(
            "my-extension", "1.2.0", "1.0.0",
            permissions=("knowledge.read", "tools.invoke"),
            dependencies=("base-connector",), entrypoint="plugin/main.py",
            dependency_versions={"base-connector": "1.0.0"},
        )
        validate_platform_compatibility(manifest, platform_api_version="1.0.0")
        with self.assertRaises(ValueError):
            validate_platform_compatibility(manifest, platform_api_version="2.0.0")
        with self.assertRaises(ValueError):
            ExtensionManifest(
                "bad-extension", "1.0", "1.0.0", ("*",), entrypoint="../escape.py",
            )


class RetrySequenceTransport:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.requests = []

    def send(self, request: Request, *, timeout: float):
        self.requests.append((request, timeout))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class SdkRetryContractTests(unittest.TestCase):
    def _client(self, transport, sleeps):
        return PlatformClient(
            base_url="https://platform.example.test",
            token="token",
            transport=transport,
            retry_attempts=2,
            retry_backoff_seconds=0.5,
            sleep=sleeps.append,
        )

    def test_get_retries_transient_server_failure_with_bounded_backoff(self):
        transport = RetrySequenceTransport([
            ApiResponse(503, {}, {"error": "busy"}),
            ApiResponse(200, {}, {"ok": True}),
        ])
        sleeps = []
        result = self._client(transport, sleeps).get("/health")
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(sleeps, [0.5])

    def test_post_retries_only_with_idempotency_key(self):
        response = ApiResponse(503, {}, {"error": "busy"})
        unsafe_transport = RetrySequenceTransport([response, ApiResponse(200, {}, {})])
        sleeps = []
        with self.assertRaises(PlatformApiError):
            self._client(unsafe_transport, sleeps).post("/runs", {"task": "x"})
        self.assertEqual(len(unsafe_transport.requests), 1)
        self.assertEqual(sleeps, [])

        safe_transport = RetrySequenceTransport([
            ApiResponse(503, {}, {"error": "busy"}),
            ApiResponse(200, {}, {"run_id": "r1"}),
        ])
        self.assertEqual(
            self._client(safe_transport, sleeps).post(
                "/runs", {"task": "x"}, idempotency_key="req-1",
            ),
            {"run_id": "r1"},
        )
        self.assertEqual(len(safe_transport.requests), 2)
        self.assertEqual(
            safe_transport.requests[0][0].get_header("Idempotency-key"),
            "req-1",
        )

    def test_retry_after_header_is_honored_and_capped(self):
        transport = RetrySequenceTransport([
            ApiResponse(429, {"Retry-After": "2"}, {"error": "rate_limited"}),
            ApiResponse(200, {}, {"ok": True}),
        ])
        sleeps = []
        self.assertEqual(self._client(transport, sleeps).get("/health"), {"ok": True})
        self.assertEqual(sleeps, [2.0])

        capped = RetrySequenceTransport([
            ApiResponse(503, {"Retry-After": "999"}, {}),
            ApiResponse(200, {}, {}),
        ])
        sleeps = []
        self.assertEqual(self._client(capped, sleeps).get("/health"), {})
        self.assertEqual(sleeps, [30.0])

    def test_retry_after_http_date_is_honored_and_invalid_or_past_dates_fall_back(self):
        future = format_http_date(
            datetime.now(timezone.utc) + timedelta(seconds=20),
            usegmt=True,
        )
        transport = RetrySequenceTransport([
            ApiResponse(503, {"Retry-After": future}, {}),
            ApiResponse(200, {}, {"ok": True}),
        ])
        sleeps = []
        self.assertEqual(self._client(transport, sleeps).get("/health"), {"ok": True})
        self.assertEqual(len(sleeps), 1)
        self.assertGreaterEqual(sleeps[0], 18.0)
        self.assertLessEqual(sleeps[0], 22.0)

        for header in ("not-a-date", format_http_date(
            datetime.now(timezone.utc) - timedelta(seconds=30),
            usegmt=True,
        )):
            with self.subTest(retry_after=header):
                fallback = RetrySequenceTransport([
                    ApiResponse(503, {"Retry-After": header}, {}),
                    ApiResponse(200, {}, {}),
                ])
                delays = []
                self.assertEqual(self._client(fallback, delays).get("/health"), {})
                self.assertEqual(delays, [0.5])

    def test_connection_errors_retry_but_client_errors_do_not(self):
        for network_error in (ConnectionError("temporary"), TimeoutError("temporary timeout")):
            with self.subTest(network_error=type(network_error).__name__):
                transport = RetrySequenceTransport([
                    network_error,
                    ApiResponse(200, {}, {"ok": True}),
                ])
                sleeps = []
                self.assertEqual(self._client(transport, sleeps).get("/health"), {"ok": True})
                self.assertEqual(len(transport.requests), 2)
                self.assertEqual(sleeps, [0.5])

        bad_request = RetrySequenceTransport([ApiResponse(400, {}, {"error": "bad"})])
        with self.assertRaises(PlatformApiError):
            self._client(bad_request, []).get("/health")
        self.assertEqual(len(bad_request.requests), 1)

    def test_retry_configuration_is_bounded(self):
        for kwargs in (
            {"retry_attempts": -1},
            {"retry_attempts": 6},
            {"retry_backoff_seconds": -0.1},
            {"retry_backoff_seconds": 5.1},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PlatformClient(
                    base_url="https://platform.example.test",
                    token="token",
                    **kwargs,
                )


if __name__ == "__main__":
    unittest.main()
