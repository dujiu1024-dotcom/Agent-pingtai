from __future__ import annotations

import json
import unittest

from fastapi import HTTPException

from agent_platform.api.contract import (
    PublicApiContractASGIMiddleware,
    decode_secure_cursor,
    encode_secure_cursor,
    error_for_http_status,
    idempotency_scope,
    validate_api_version,
    validate_page,
)
from agent_platform.api.idempotency import (
    ApiIdempotencyService,
    IdempotencyClaim,
    IdempotencyResponse,
)


class RateLimiter:
    def __init__(self, allowed=True):
        self.allowed = allowed
        self.calls = []

    async def allow(self, key, *, limit, window_seconds):
        self.calls.append((key, limit, window_seconds))
        return self.allowed


async def invoke_asgi(app, *, path="/api/platform/v1/runs", headers=(), method="GET"):
    messages = []
    scope = {
        "type": "http",
        "path": path,
        "method": method,
        "headers": list(headers),
        "client": ("192.0.2.10", 1234),
        "state": {},
    }

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = next(message for message in messages if message["type"] == "http.response.body")
    return start, json.loads(body.get("body", b"{}") or b"{}"), scope


class IdempotencyBackend:
    def __init__(self):
        self.rows = {}

    @staticmethod
    def scope(kwargs):
        return tuple(kwargs[name] for name in (
            "tenant_id", "principal_id", "method", "route", "key",
        ))

    async def claim(self, **kwargs):
        scope = self.scope(kwargs)
        row = self.rows.get(scope)
        if row is None:
            self.rows[scope] = {
                "request_hash": kwargs["request_hash"],
                "owner_token": kwargs["owner_token"],
            }
            return IdempotencyClaim("owner", owner_token=kwargs["owner_token"])
        if row["request_hash"] != kwargs["request_hash"]:
            return IdempotencyClaim("conflict")
        if "response" in row:
            return IdempotencyClaim("replay", response=row["response"])
        return IdempotencyClaim("in_progress")

    async def complete(self, **kwargs):
        row = self.rows[self.scope(kwargs)]
        if row.get("owner_token") != kwargs["owner_token"]:
            return False
        row["response"] = kwargs["response"]
        row.pop("owner_token")
        return True

    async def release(self, **kwargs):
        row = self.rows.get(self.scope(kwargs))
        if row is None or row.get("owner_token") != kwargs["owner_token"]:
            return False
        self.rows.pop(self.scope(kwargs), None)
        return True


class Issue20A2001PublicApiContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_version_path_header_and_request_correlation_are_enforced(self):
        limiter = RateLimiter()

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        middleware = PublicApiContractASGIMiddleware(app, rate_limiter=limiter)
        start, _, scope = await invoke_asgi(
            middleware,
            headers=[
                (b"x-api-version", b"v1"),
                (b"x-request-id", b"contract-request-01"),
            ],
        )
        headers = dict(start["headers"])
        self.assertEqual(start["status"], 200)
        self.assertEqual(headers[b"x-api-version"], b"v1")
        self.assertEqual(headers[b"x-request-id"], b"contract-request-01")
        self.assertEqual(scope["state"]["api.request_id"], "contract-request-01")
        self.assertEqual(validate_api_version("v1"), "v1")

        unsupported, payload, _ = await invoke_asgi(
            middleware, path="/api/platform/v2/runs",
        )
        self.assertEqual(unsupported["status"], 404)
        self.assertEqual(payload["error"]["code"], "UNSUPPORTED_API_VERSION")

        mismatch, payload, _ = await invoke_asgi(
            middleware, headers=[(b"x-api-version", b"v2")],
        )
        self.assertEqual(mismatch["status"], 400)
        self.assertEqual(payload["error"]["code"], "UNSUPPORTED_API_VERSION")

    async def test_rate_limit_returns_429_stable_error_and_retry_headers(self):
        limiter = RateLimiter(allowed=False)

        async def app(scope, receive, send):
            raise AssertionError("rate-limited request must not reach the application")

        middleware = PublicApiContractASGIMiddleware(
            app, rate_limiter=limiter, limit=7, window_seconds=23,
        )
        start, payload, _ = await invoke_asgi(
            middleware, headers=[(b"x-request-id", b"rate-limit-01")],
        )
        headers = dict(start["headers"])
        self.assertEqual(start["status"], 429)
        self.assertEqual(payload["error"]["code"], "RATE_LIMITED")
        self.assertEqual(payload["error"]["request_id"], "rate-limit-01")
        self.assertEqual(headers[b"ratelimit-limit"], b"7")
        self.assertEqual(headers[b"ratelimit-remaining"], b"0")
        self.assertEqual(headers[b"retry-after"], b"23")
        self.assertEqual(len(limiter.calls), 1)

    async def test_authentication_authorization_and_throttling_have_distinct_stable_codes(self):
        expected = {
            401: "UNAUTHENTICATED",
            403: "FORBIDDEN",
            429: "RATE_LIMITED",
        }
        for status, code in expected.items():
            with self.subTest(status=status):
                error = error_for_http_status(
                    status, "safe client message", request_id="auth-contract-01",
                )
                self.assertEqual((error.status, error.code), (status, code))
                payload = error.to_dict()
                self.assertEqual(payload["error"]["request_id"], "auth-contract-01")
                self.assertEqual(payload["error"]["message"], "safe client message")

    async def test_pagination_limits_and_signed_cursor_context_are_stable(self):
        self.assertEqual(validate_page(), (50, None))
        self.assertEqual(validate_page(limit=25, cursor="opaque-cursor"), (25, "opaque-cursor"))
        for invalid_limit in (0, 201, True, "10"):
            with self.subTest(limit=invalid_limit), self.assertRaises(ValueError):
                validate_page(limit=invalid_limit)
        with self.assertRaises(ValueError):
            validate_page(cursor=" ")

        secret = b"c" * 32
        cursor = encode_secure_cursor(
            {"created_at": "2026-11-18T00:00:00Z", "id": "row-9"},
            secret=secret,
            tenant_id="tenant-a",
            query_fingerprint="status=open",
            now=100,
        )
        self.assertEqual(
            decode_secure_cursor(
                cursor, secret=secret, tenant_id="tenant-a",
                query_fingerprint="status=open", now=101,
            ),
            {"created_at": "2026-11-18T00:00:00Z", "id": "row-9"},
        )
        for tenant_id, fingerprint in (
            ("tenant-b", "status=open"),
            ("tenant-a", "status=closed"),
        ):
            with self.subTest(tenant_id=tenant_id, fingerprint=fingerprint):
                with self.assertRaises(ValueError):
                    decode_secure_cursor(
                        cursor, secret=secret, tenant_id=tenant_id,
                        query_fingerprint=fingerprint, now=101,
                    )

    async def test_idempotency_replays_exact_response_and_isolates_scope(self):
        backend = IdempotencyBackend()
        service = ApiIdempotencyService(backend)
        calls = []

        async def operation():
            calls.append("executed")
            return IdempotencyResponse(
                201, {"content-type": "application/json"}, b'{"resource_id":"r-1"}',
            )

        args = {
            "tenant_id": "tenant-a",
            "principal_id": "user-a",
            "method": "POST",
            "route": "/api/platform/v1/runs",
            "key": "create-run-001",
            "body": b'{"name":"one"}',
            "operation": operation,
        }
        first, first_replay = await service.execute(**args)
        repeated, repeated_replay = await service.execute(**args)
        self.assertFalse(first_replay)
        self.assertTrue(repeated_replay)
        self.assertEqual(repeated, first)
        self.assertEqual(calls, ["executed"])

        other_tenant, tenant_replay = await service.execute(
            **{**args, "tenant_id": "tenant-b"},
        )
        self.assertFalse(tenant_replay)
        self.assertEqual(other_tenant.status_code, 201)
        self.assertEqual(calls, ["executed", "executed"])

        with self.assertRaises(HTTPException) as conflict:
            await service.execute(**{**args, "body": b'{"name":"different"}'})
        self.assertEqual(conflict.exception.status_code, 409)
        self.assertEqual(
            conflict.exception.detail["code"], "IDEMPOTENCY_KEY_CONFLICT",
        )

        self.assertEqual(
            idempotency_scope(
                tenant_id="tenant-a", principal_id="user-a", method="post",
                route="/api/platform/v1/runs", key="create-run-001",
            ),
            ("tenant-a", "user-a", "POST", "/api/platform/v1/runs", "create-run-001"),
        )


if __name__ == "__main__":
    unittest.main()
