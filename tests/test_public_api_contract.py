from __future__ import annotations

import unittest

from agent_platform.api.contract import (
    API_V1_PREFIX,
    api_version_lifecycle_headers,
    ApiError,
    PublicApiContractASGIMiddleware,
    error_for_http_status,
    idempotency_scope,
    rate_limit_headers,
    api_rate_limit_key,
    validate_api_version,
    validate_idempotency_key,
    validate_page,
)


class PublicApiContractTests(unittest.TestCase):
    def test_deprecation_lifecycle_headers_are_explicit_and_rfc_formatted(self):
        from datetime import datetime, timezone
        deprecated = datetime(2026, 1, 1, tzinfo=timezone.utc)
        sunset = datetime(2027, 1, 1, tzinfo=timezone.utc)
        headers = api_version_lifecycle_headers(
            deprecated_at=deprecated, sunset_at=sunset,
            successor_url="/api/platform/v2",
        )
        self.assertEqual(headers["Deprecation"], "@1767225600")
        self.assertEqual(headers["Sunset"], "Fri, 01 Jan 2027 00:00:00 GMT")
        self.assertEqual(headers["Link"], '</api/platform/v2>; rel="successor-version"')

    def test_deprecation_metadata_rejects_unsafe_or_inconsistent_values(self):
        from datetime import datetime, timezone
        aware = datetime(2027, 1, 1, tzinfo=timezone.utc)
        naive = datetime(2026, 1, 1)
        with self.assertRaises(ValueError):
            api_version_lifecycle_headers(sunset_at=aware)
        with self.assertRaises(ValueError):
            api_version_lifecycle_headers(deprecated_at=naive)
        with self.assertRaises(ValueError):
            api_version_lifecycle_headers(deprecated_at=aware, sunset_at=aware)
        with self.assertRaises(ValueError):
            api_version_lifecycle_headers(deprecated_at=aware, successor_url="//evil.example/v2")
        with self.assertRaises(ValueError):
            api_version_lifecycle_headers(deprecated_at=aware, successor_url="/v2\\r\\nX-Evil: yes")

    def test_version_is_explicit_and_v1_is_supported(self):
        self.assertEqual(API_V1_PREFIX, "/api/platform/v1")
        self.assertEqual(validate_api_version("v1"), "v1")
        with self.assertRaises(ValueError):
            validate_api_version("v3")

    def test_pagination_is_bounded_and_cursor_is_opaque(self):
        self.assertEqual(validate_page(limit=25, cursor="opaque-token"), (25, "opaque-token"))
        self.assertEqual(validate_page(), (50, None))
        for limit in (0, 201, True, "10"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                validate_page(limit=limit)
        with self.assertRaises(ValueError):
            validate_page(cursor=" ")

    def test_signed_cursor_is_tamper_evident_expiring_and_context_bound(self):
        secret = b"k" * 32
        token = encode_secure_cursor(
            {"created_at": "2026-11-17T00:00:00Z", "id": "row-7"},
            secret=secret, tenant_id="tenant-a", query_fingerprint="status=open", now=100,
        )
        self.assertEqual(
            decode_secure_cursor(token, secret=secret, tenant_id="tenant-a",
                                 query_fingerprint="status=open", now=101),
            {"created_at": "2026-11-17T00:00:00Z", "id": "row-7"},
        )
        for kwargs in (
            {"tenant_id": "tenant-b", "query_fingerprint": "status=open", "now": 101},
            {"tenant_id": "tenant-a", "query_fingerprint": "status=closed", "now": 101},
            {"tenant_id": "tenant-a", "query_fingerprint": "status=open", "now": 3700},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                decode_secure_cursor(token, secret=secret, **kwargs)
        altered = token[:-1] + ("0" if token[-1] != "0" else "1")
        with self.assertRaises(ValueError):
            decode_secure_cursor(altered, secret=secret, tenant_id="tenant-a",
                                 query_fingerprint="status=open", now=101)
        with self.assertRaises(ValueError):
            decode_secure_cursor("非ASCII." + ("0" * 64), secret=secret,
                                 tenant_id="tenant-a", query_fingerprint="status=open", now=101)
        with self.assertRaises(ValueError):
            decode_secure_cursor(token, secret=secret, tenant_id="tenant-a",
                                 query_fingerprint="status=open", now=True)
        with self.assertRaises(ValueError):
            encode_secure_cursor({"id": 1}, secret=b"weak", tenant_id="tenant-a",
                                query_fingerprint="all")

    def test_idempotency_key_and_scope_are_tenant_principal_route_bound(self):
        key = validate_idempotency_key("client:request-001")
        scope = idempotency_scope(
            tenant_id="tenant-a", principal_id="user-1", method="post",
            route="/api/platform/v1/runs", key=key,
        )
        self.assertEqual(scope, ("tenant-a", "user-1", "POST", "/api/platform/v1/runs", key))
        with self.assertRaises(ValueError):
            validate_idempotency_key("bad key with spaces")

    def test_errors_are_stable_and_hide_server_exception_details(self):
        error = error_for_http_status(503, "database password leaked", request_id="req-1")
        self.assertEqual(error.code, "SERVICE_UNAVAILABLE")
        self.assertNotIn("password", error.to_dict()["error"]["message"])
        self.assertEqual(error.to_dict()["error"]["request_id"], "req-1")
        self.assertEqual(ApiError("NOT_FOUND", "missing", 404).to_dict(),
                         {"error": {"code": "NOT_FOUND", "message": "missing"}})

    def test_common_http_failures_have_distinct_stable_codes(self):
        expected = {
            405: "METHOD_NOT_ALLOWED",
            408: "REQUEST_TIMEOUT",
            413: "PAYLOAD_TOO_LARGE",
            415: "UNSUPPORTED_MEDIA_TYPE",
            416: "RANGE_NOT_SATISFIABLE",
            423: "RESOURCE_LOCKED",
            424: "FAILED_DEPENDENCY",
            428: "PRECONDITION_REQUIRED",
            431: "REQUEST_HEADER_FIELDS_TOO_LARGE",
        }
        for status, code in expected.items():
            with self.subTest(status=status):
                self.assertEqual(
                    error_for_http_status(status, "safe client message").code, code,
                )

    def test_unknown_client_status_is_generic_and_server_message_is_redacted(self):
        self.assertEqual(error_for_http_status(418, "teapot").code, "REQUEST_REJECTED")
        error = error_for_http_status(507, "internal storage path /srv/private")
        self.assertEqual(error.code, "INTERNAL_ERROR")
        self.assertEqual(error.message, "The request could not be completed")

    def test_rate_limit_headers_are_standardized(self):
        self.assertEqual(rate_limit_headers(limit=100, remaining=0, reset_epoch=123, retry_after=30),
                         {"RateLimit-Limit": "100", "RateLimit-Remaining": "0",
                          "RateLimit-Reset": "123", "Retry-After": "30"})
        with self.assertRaises(ValueError):
            rate_limit_headers(limit=10, remaining=11, reset_epoch=1)


class PublicApiMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    async def _request(self, middleware, path="/api/platform/v1/runs", headers=()):
        sent = []
        scope = {
            "type": "http", "path": path, "method": "GET",
            "headers": list(headers), "client": ("192.0.2.10", 1234), "state": {},
        }

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        await middleware(scope, receive, send)
        return sent, scope

    async def test_version_negotiation_and_response_correlation_headers(self):
        class Limiter:
            async def allow(self, key, *, limit, window_seconds):
                self.last = (key, limit, window_seconds)
                return True

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        limiter = Limiter()
        middleware = PublicApiContractASGIMiddleware(app, rate_limiter=limiter)
        response, scope = await self._request(
            middleware, headers=[(b"x-api-version", b"v1"), (b"x-request-id", b"request-7")],
        )
        self.assertEqual(response[0]["status"], 200)
        response_headers = dict(response[0]["headers"])
        self.assertEqual(response_headers[b"x-api-version"], b"v1")
        self.assertEqual(response_headers[b"x-request-id"], b"request-7")
        self.assertEqual(
            limiter.last,
            (api_rate_limit_key(
                client_ip="192.0.2.10", method="GET",
                path="/api/platform/v1/runs",
                authorization=None, cookie=None,
            ), 120, 60),
        )
        self.assertEqual(scope["state"]["api.request_id"], "request-7")

    async def test_unsupported_path_and_mismatched_header_are_rejected(self):
        class Limiter:
            async def allow(self, key, *, limit, window_seconds):
                return True

        async def app(scope, receive, send):
            raise AssertionError("invalid version must not reach the application")

        middleware = PublicApiContractASGIMiddleware(app, rate_limiter=Limiter())
        unsupported, _ = await self._request(middleware, path="/api/platform/v2/runs")
        self.assertEqual(unsupported[0]["status"], 404)
        self.assertIn(b"UNSUPPORTED_API_VERSION", unsupported[1]["body"])
        mismatch, _ = await self._request(
            middleware, headers=[(b"x-api-version", b"v2")],
        )
        self.assertEqual(mismatch[0]["status"], 400)
        self.assertIn(b"UNSUPPORTED_API_VERSION", mismatch[1]["body"])

    async def test_rate_limit_key_scopes_credentials_without_exposing_tokens(self):
        first = api_rate_limit_key(
            client_ip="192.0.2.10", method="POST",
            path="/api/platform/v1/webhooks",
            authorization=b"Bearer tenant-a-secret",
        )
        second = api_rate_limit_key(
            client_ip="192.0.2.10", method="POST",
            path="/api/platform/v1/webhooks",
            authorization=b"Bearer tenant-b-secret",
        )
        other_resource = api_rate_limit_key(
            client_ip="192.0.2.10", method="POST",
            path="/api/platform/v1/runs",
            authorization=b"Bearer tenant-a-secret",
        )
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, other_resource)
        self.assertNotIn("tenant-a-secret", first)
        self.assertIn("192.0.2.10", first)

    async def test_rate_limit_returns_retry_headers_and_stable_error(self):
        class Limiter:
            async def allow(self, key, *, limit, window_seconds):
                return False

        async def app(scope, receive, send):
            raise AssertionError("limited request must not reach the application")

        middleware = PublicApiContractASGIMiddleware(app, rate_limiter=Limiter())
        response, _ = await self._request(middleware)
        self.assertEqual(response[0]["status"], 429)
        headers = dict(response[0]["headers"])
        self.assertEqual(headers[b"ratelimit-limit"], b"120")
        self.assertEqual(headers[b"ratelimit-remaining"], b"0")
        self.assertEqual(headers[b"retry-after"], b"60")
        self.assertIn(b"RATE_LIMITED", response[1]["body"])

    async def test_rate_limit_backend_failure_fails_closed(self):
        class BrokenLimiter:
            async def allow(self, key, *, limit, window_seconds):
                raise RuntimeError("redis password leaked")

        async def app(scope, receive, send):
            raise AssertionError("backend failure must not reach the application")

        middleware = PublicApiContractASGIMiddleware(app, rate_limiter=BrokenLimiter())
        response, _ = await self._request(middleware)
        self.assertEqual(response[0]["status"], 503)
        self.assertIn(b"SERVICE_UNAVAILABLE", response[1]["body"])
        self.assertNotIn(b"password", response[1]["body"])


class PublicApiSdkVersionHeaderTests(unittest.TestCase):
    def test_sdk_sends_the_version_token_expected_by_http_contract(self):
        class CaptureTransport:
            def __init__(self):
                self.version_header = None

            def send(self, request, *, timeout):
                self.version_header = request.get_header("X-api-version")
                from agent_platform.sdk.client import ApiResponse
                return ApiResponse(200, {}, {"ok": True})

        transport = CaptureTransport()
        client = PlatformClient(
            base_url="https://platform.example.test",
            token="test-token",
            transport=transport,
            retry_attempts=0,
        )
        self.assertEqual(client.get("/runs"), {"ok": True})
        self.assertEqual(transport.version_header, "v1")


if __name__ == "__main__":
    unittest.main()
