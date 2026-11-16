from __future__ import annotations

import unittest
from urllib.request import Request

from agent_platform.sdk.client import ApiResponse, PlatformApiError, PlatformClient


class SequenceTransport:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[Request] = []

    def send(self, request: Request, *, timeout: float) -> ApiResponse:
        self.requests.append(request)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class SdkRetryMethodMatrixTests(unittest.TestCase):
    def make_client(self, transport: SequenceTransport, sleeps: list[float]) -> PlatformClient:
        return PlatformClient(
            base_url="https://platform.example.test",
            token="test-token",
            transport=transport,
            retry_attempts=2,
            retry_backoff_seconds=0.1,
            sleep=sleeps.append,
        )

    def test_get_put_and_delete_are_retried_as_idempotent_methods(self):
        for method in ("GET", "PUT", "DELETE"):
            with self.subTest(method=method):
                transport = SequenceTransport(
                    ApiResponse(502, {}, {"error": "gateway"}),
                    ApiResponse(200, {}, {"ok": True}),
                )
                sleeps: list[float] = []
                client = self.make_client(transport, sleeps)
                result = client.request(
                    method,
                    "/resources/one",
                    body={"enabled": True} if method == "PUT" else None,
                )
                self.assertEqual(result, {"ok": True})
                self.assertEqual(len(transport.requests), 2)
                self.assertEqual(sleeps, [0.1])

    def test_patch_is_not_retried_without_idempotency_key(self):
        transport = SequenceTransport(
            ApiResponse(503, {}, {"error": "busy"}),
            ApiResponse(200, {}, {"ok": True}),
        )
        sleeps: list[float] = []
        with self.assertRaises(PlatformApiError):
            self.make_client(transport, sleeps).request(
                "PATCH", "/resources/one", body={"enabled": False},
            )
        self.assertEqual(len(transport.requests), 1)
        self.assertEqual(sleeps, [])

    def test_patch_retries_only_when_idempotency_key_is_supplied(self):
        transport = SequenceTransport(
            ApiResponse(504, {}, {"error": "gateway_timeout"}),
            ApiResponse(200, {}, {"ok": True}),
        )
        sleeps: list[float] = []
        result = self.make_client(transport, sleeps).request(
            "PATCH",
            "/resources/one",
            body={"enabled": False},
            idempotency_key="patch-resource-one-v1",
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(transport.requests), 2)
        self.assertEqual(
            transport.requests[0].get_header("Idempotency-key"),
            "patch-resource-one-v1",
        )
        self.assertEqual(sleeps, [0.1])


if __name__ == "__main__":
    unittest.main()
