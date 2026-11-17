"""Tests for the public API contract primitives."""
import asyncio
import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from agent_platform.api.public_contract import ApiError, InMemoryIdempotencyStore, PublicApiContractMiddleware, build_page, decode_cursor, encode_cursor, install_api_error_handlers, resolve_idempotency, save_idempotency

class PublicApiContractTests(unittest.TestCase):
    def test_version_negotiation_and_request_id(self):
        app = FastAPI()
        app.add_middleware(PublicApiContractMiddleware)
        @app.get("/api/platform/v1/ping")
        async def ping(): return {"ok": True}
        client = TestClient(app)
        response = client.get("/api/platform/v1/ping", headers={"X-API-Version": "1", "X-Request-ID": "req-123"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-request-id"], "req-123")
        self.assertEqual(response.headers["x-api-version"], "1")
        unsupported = client.get("/api/platform/v1/ping", headers={"X-API-Version": "99"})
        self.assertEqual(unsupported.status_code, 400)
        self.assertEqual(unsupported.json()["error"]["code"], "unsupported_api_version")

    def test_error_envelope(self):
        app = FastAPI()
        install_api_error_handlers(app)
        @app.get("/fail")
        async def fail(): raise ApiError(403, "forbidden", "Permission denied")
        response = TestClient(app).get("/fail")
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"]["code"], "forbidden")

    def test_cursor_round_trip_and_validation(self):
        value = {"created_at": "2026-11-17T00:00:00Z", "id": "abc"}
        self.assertEqual(decode_cursor(encode_cursor(value)), value)
        with self.assertRaises(ValueError): decode_cursor("not-a-valid-cursor")

    def test_page_limit_and_has_more(self):
        page = build_page([1, 2, 3], limit=2, cursor_values={"id": 2})
        self.assertEqual(page.items, [1, 2])
        self.assertTrue(page.has_more)
        self.assertEqual(decode_cursor(page.next_cursor), {"id": 2})
        with self.assertRaises(ValueError): build_page([], limit=201)

    def test_idempotency_replays_and_detects_conflict(self):
        async def scenario():
            store = InMemoryIdempotencyStore()
            self.assertIsNone(await resolve_idempotency(store, tenant_id="t1", key="k1", method="POST", path="/items", body=b'{"a":1}'))
            await save_idempotency(store, tenant_id="t1", key="k1", method="POST", path="/items", body=b'{"a":1}', response={"id": "created"})
            self.assertEqual(await resolve_idempotency(store, tenant_id="t1", key="k1", method="POST", path="/items", body=b'{"a":1}'), {"id": "created"})
            with self.assertRaises(ApiError) as raised:
                await resolve_idempotency(store, tenant_id="t1", key="k1", method="POST", path="/items", body=b'{"a":2}')
            self.assertEqual(raised.exception.status_code, 409)
        asyncio.run(scenario())

if __name__ == "__main__": unittest.main()
