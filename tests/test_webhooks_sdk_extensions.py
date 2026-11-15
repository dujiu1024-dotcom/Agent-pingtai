from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from urllib.request import Request

from agent_platform.events.webhooks import (
    WebhookAttempt, WebhookDispatcher, WebhookPayload, WebhookSubscription,
    signature, verify_signature,
)
from agent_platform.sdk.client import ApiResponse, PlatformApiError, PlatformClient
from agent_platform.extensions.manifest import ExtensionManifest, validate_platform_compatibility


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
        )
        self.completed = []
        self.retried = []
        self.dead = []

    async def claim(self, *, limit):
        return [(self.subscription, self.payload, self.attempt)]

    async def complete(self, tenant_id, delivery_id, *, response_code):
        self.completed.append((tenant_id, delivery_id, response_code))

    async def retry(self, tenant_id, delivery_id, *, next_attempt_at, response_code):
        self.retried.append((tenant_id, delivery_id, next_attempt_at, response_code))

    async def dead_letter(self, tenant_id, delivery_id, *, response_code):
        self.dead.append((tenant_id, delivery_id, response_code))


class FakeWebhookTransport:
    def __init__(self, code=204):
        self.code = code
        self.requests = []

    async def post(self, url, *, body, headers, timeout_seconds):
        self.requests.append((url, body, headers, timeout_seconds))
        return self.code


class WebhookTests(unittest.IsolatedAsyncioTestCase):
    def test_signature_verification_is_constant_contract(self):
        body = b'{"event":"ok"}'
        signed = signature(SECRET, "1794787200", body)
        self.assertTrue(verify_signature(SECRET, "1794787200", body, signed))
        self.assertFalse(verify_signature(SECRET, "1794787200", body + b"x", signed))
        self.assertFalse(verify_signature(SECRET, "not-a-time", body, signed))

    def test_subscription_rejects_non_https_and_local_targets(self):
        for url in ("http://hooks.example.test", "https://localhost/hook",
                    "https://user:pass@hooks.example.test/hook"):
            with self.assertRaises(ValueError):
                WebhookSubscription("s", "t", url, "secret://ref", ("topic",))

    async def test_dispatch_signs_and_deduplicates_with_event_id(self):
        store = FakeWebhookStore()
        transport = FakeWebhookTransport()
        dispatcher = WebhookDispatcher(
            store=store, transport=transport, resolve_secret=lambda _ref: SECRET,
            clock=lambda: datetime(2026, 11, 16, tzinfo=timezone.utc),
        )
        result = await dispatcher.dispatch()
        self.assertEqual(result["delivered"], 1)
        _, body, headers, _ = transport.requests[0]
        self.assertEqual(headers["Idempotency-Key"], "event-1")
        self.assertTrue(verify_signature(
            SECRET, headers["X-Platform-Timestamp"], body,
            headers["X-Platform-Signature"],
        ))
        self.assertEqual(json.loads(body)["id"], "event-1")
        self.assertEqual(store.completed[0][0], "tenant-a")

    async def test_dispatch_retries_then_dead_letters(self):
        store = FakeWebhookStore(attempt=8)
        transport = FakeWebhookTransport(code=503)
        dispatcher = WebhookDispatcher(
            store=store, transport=transport, resolve_secret=lambda _ref: SECRET,
            max_attempts=8,
        )
        result = await dispatcher.dispatch()
        self.assertEqual(result["dead_lettered"], 1)
        self.assertEqual(len(store.dead), 1)


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

    def test_sdk_surfaces_api_errors(self):
        client = PlatformClient(
            base_url="https://platform.example.test", token="x",
            transport=FakeSdkTransport(status=403),
        )
        with self.assertRaises(PlatformApiError):
            client.get("/me")

    def test_extension_manifest_requires_declared_permissions_and_compatible_api(self):
        manifest = ExtensionManifest(
            "my-extension", "1.2.0", "1.0.0",
            permissions=("knowledge.read", "tools.invoke"),
            dependencies=("base-connector",), entrypoint="plugin/main.py",
        )
        validate_platform_compatibility(manifest, platform_api_version="1.0.0")
        with self.assertRaises(ValueError):
            validate_platform_compatibility(manifest, platform_api_version="2.0.0")
        with self.assertRaises(ValueError):
            ExtensionManifest(
                "bad-extension", "1.0", "1.0.0", ("*",), entrypoint="../escape.py",
            )


if __name__ == "__main__":
    unittest.main()
