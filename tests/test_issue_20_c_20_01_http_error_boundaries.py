"""Regression coverage for public API authentication and throttling errors."""
import unittest

from agent_platform.api.contract import error_for_http_status
from agent_platform.sdk.client import (
    ApiResponse, AuthenticationError, AuthorizationError, PlatformClient,
    RateLimitError,
)


class _Transport:
    def __init__(self, status):
        self.status = status

    def send(self, request, *, timeout):
        return ApiResponse(
            self.status,
            {"X-Request-ID": "req-boundary-1", "Retry-After": "30"},
            {"error": {"code": {
                401: "UNAUTHENTICATED",
                403: "FORBIDDEN",
                429: "RATE_LIMITED",
            }[self.status], "message": "request rejected"}},
        )


class PublicApiHttpErrorBoundaryTests(unittest.TestCase):
    def test_contract_maps_authentication_authorization_and_rate_limit(self):
        expected = {
            401: ("UNAUTHENTICATED", "Authentication is required"),
            403: ("FORBIDDEN", "Access is denied"),
            429: ("RATE_LIMITED", "Rate limit exceeded"),
        }
        for status, (code, message) in expected.items():
            with self.subTest(status=status):
                error = error_for_http_status(status, message, request_id="req-1")
                self.assertEqual((error.status, error.code, error.message),
                                 (status, code, message))
                self.assertEqual(error.to_dict()["error"]["request_id"], "req-1")

    def test_sdk_preserves_distinct_401_403_429_error_types_and_metadata(self):
        expected = {
            401: AuthenticationError,
            403: AuthorizationError,
            429: RateLimitError,
        }
        for status, error_type in expected.items():
            with self.subTest(status=status):
                client = PlatformClient(
                    base_url="https://platform.example.test",
                    token="test-token",
                    transport=_Transport(status),
                    retry_attempts=0,
                )
                with self.assertRaises(error_type) as caught:
                    client.request("GET", "/runs")
                self.assertEqual(caught.exception.status_code, status)
                self.assertEqual(caught.exception.request_id, "req-boundary-1")
                self.assertEqual(caught.exception.code, {
                    401: "UNAUTHENTICATED", 403: "FORBIDDEN", 429: "RATE_LIMITED",
                }[status])


if __name__ == "__main__":
    unittest.main()
