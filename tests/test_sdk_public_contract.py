"""Compatibility tests for the typed Python SDK."""

import unittest
from urllib.request import Request

from agent_platform.sdk import (
    API_VERSION, API_VERSION_HEADER, ApiResponse, AuthenticationError, AuthorizationError,
    ConflictError, Page, PlatformApiError, PlatformClient, RateLimitError, ServerError,
)


class FakeTransport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def send(self, request: Request, *, timeout: float) -> ApiResponse:
        self.requests.append((request, timeout))
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class PythonSdkContractTests(unittest.TestCase):
    def test_version_auth_timeout_and_request_id_headers(self):
        transport = FakeTransport([ApiResponse(200, {}, {"ok": True})])
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, timeout=4, retry_attempts=0)
        self.assertEqual(client.get("/health"), {"ok": True})
        request, timeout = transport.requests[0]
        self.assertEqual(timeout, 4)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(API_VERSION, "v1")
        self.assertEqual(API_VERSION_HEADER, "1")
        self.assertEqual(request.get_header("X-api-version"), API_VERSION_HEADER)
        self.assertTrue(request.get_header("X-request-id"))

    def test_structured_error_types_and_metadata(self):
        transport = FakeTransport([ApiResponse(401, {"X-Request-ID": "req-1"}, {
            "error": {"code": "token_expired", "message": "Token expired", "details": {"hint": "renew"}},
        })])
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, retry_attempts=0)
        with self.assertRaises(AuthenticationError) as raised:
            client.get("/private")
        self.assertEqual(raised.exception.code, "token_expired")
        self.assertEqual(raised.exception.request_id, "req-1")
        self.assertEqual(raised.exception.details, {"hint": "renew"})
        with self.assertRaises(ConflictError):
            self._raise_conflict()

    @staticmethod
    def _raise_conflict():
        raise ConflictError(409, {"error": {"code": "conflict", "message": "conflict"}})

    def test_rate_limit_retries_and_idempotent_post_key(self):
        transport = FakeTransport([
            ApiResponse(429, {"Retry-After": "0"}, {"error": {"code": "rate_limited"}}),
            ApiResponse(201, {}, {"id": "new"}),
        ])
        delays = []
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, retry_attempts=1, sleep=delays.append)
        self.assertEqual(client.post("/items", {"name": "x"}, idempotency_key="create-1"), {"id": "new"})
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(transport.requests[0][0].get_header("Idempotency-key"), "create-1")
        self.assertEqual(delays, [0.0])

    def test_post_without_idempotency_key_is_not_retried(self):
        transport = FakeTransport([ApiResponse(503, {}, {"error": {"code": "unavailable"}})])
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, retry_attempts=3, sleep=lambda _: None)
        with self.assertRaises(PlatformApiError):
            client.post("/items", {"name": "x"})
        self.assertEqual(len(transport.requests), 1)

    def test_rate_limit_error_after_retry_budget(self):
        transport = FakeTransport([
            ApiResponse(429, {}, {"error": {"code": "rate_limited", "message": "slow"}}),
            ApiResponse(429, {}, {"error": {"code": "rate_limited", "message": "slow"}}),
        ])
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, retry_attempts=1, sleep=lambda _: None)
        with self.assertRaises(RateLimitError):
            client.get("/items")
        self.assertEqual(len(transport.requests), 2)

    def test_put_patch_delete_helpers_and_retry_safety(self):
        transport = FakeTransport([
            ApiResponse(200, {}, {"updated": True}),
            ApiResponse(200, {}, {"patched": True}),
            ApiResponse(204, {}, None),
            ApiResponse(503, {}, {"error": {"code": "unavailable"}}),
        ])
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, retry_attempts=2, sleep=lambda _: None)
        self.assertEqual(client.put("/items/1", {"name": "new"}), {"updated": True})
        self.assertEqual(client.patch("/items/1", {"name": "new"}), {"patched": True})
        self.assertIsNone(client.delete("/items/1"))
        with self.assertRaises(ServerError):
            client.patch("/items/1", {"name": "retry-not-safe"})
        self.assertEqual(len(transport.requests), 4)
        self.assertEqual([entry[0].get_method() for entry in transport.requests],
                         ["PUT", "PATCH", "DELETE", "PATCH"])

    def test_patch_with_idempotency_key_can_retry_and_preserves_key(self):
        transport = FakeTransport([
            ApiResponse(503, {}, {"error": {"code": "unavailable"}}),
            ApiResponse(200, {}, {"ok": True}),
        ])
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, retry_attempts=1, sleep=lambda _: None)
        self.assertEqual(client.patch("/items/1", {"name": "safe"}, idempotency_key="patch-1"),
                         {"ok": True})
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(transport.requests[0][0].get_header("Idempotency-key"), "patch-1")
        self.assertEqual(transport.requests[1][0].get_header("Idempotency-key"), "patch-1")

    def test_timeout_retries_bounded_and_authorization_error(self):
        transport = FakeTransport([
            TimeoutError("first timeout"),
            ApiResponse(403, {}, {"error": {"code": "forbidden", "message": "denied"}}),
        ])
        delays = []
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, retry_attempts=1, sleep=delays.append)
        with self.assertRaises(AuthorizationError):
            client.get("/private")
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(delays, [0.25])

    def test_page_iteration(self):
        transport = FakeTransport([
            ApiResponse(200, {}, {"items": [{"id": 1}], "next_cursor": "next", "has_more": True}),
            ApiResponse(200, {}, {"items": [{"id": 2}], "next_cursor": None, "has_more": False}),
        ])
        client = PlatformClient(base_url="https://api.example.test", token="secret",
                                transport=transport, retry_attempts=0)
        self.assertEqual(list(client.iter_pages("/items", limit=1)), [{"id": 1}, {"id": 2}])
        self.assertIn("cursor=next", transport.requests[1][0].full_url)
        self.assertIsInstance(Page((), None, False), Page)


if __name__ == "__main__":
    unittest.main()
