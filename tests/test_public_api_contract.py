from __future__ import annotations

import unittest

from agent_platform.api.contract import (
    API_V1_PREFIX, ApiError, error_for_http_status, idempotency_scope,
    rate_limit_headers, validate_api_version, validate_idempotency_key, validate_page,
)


class PublicApiContractTests(unittest.TestCase):
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

    def test_rate_limit_headers_are_standardized(self):
        self.assertEqual(rate_limit_headers(limit=100, remaining=0, reset_epoch=123, retry_after=30),
                         {"RateLimit-Limit": "100", "RateLimit-Remaining": "0",
                          "RateLimit-Reset": "123", "Retry-After": "30"})
        with self.assertRaises(ValueError):
            rate_limit_headers(limit=10, remaining=11, reset_epoch=1)


if __name__ == "__main__":
    unittest.main()
