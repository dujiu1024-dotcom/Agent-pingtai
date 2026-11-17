"""C-30-01 contract tests for the shared SSE protocol and reconnect client."""

from __future__ import annotations

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.streaming.client import SSEClient
from agent_platform.streaming.sse import (
    HEARTBEAT_FRAME,
    SSEEvent,
    encode_sse,
    iter_sse,
    sse_response,
)


class SSEProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_event_envelope_validation_and_utf8_framing(self):
        event = SSEEvent(
            event="run_error",
            data={"message": "模型调用失败", "retryable": False},
            event_id="run-42:7",
            retry_ms=1200,
        )
        encoded = encode_sse(event).decode("utf-8")
        self.assertTrue(encoded.startswith("id: run-42:7\nretry: 1200\nevent: run_error\n"))
        self.assertIn('data: {"message":"模型调用失败","retryable":false}', encoded)
        self.assertTrue(encoded.endswith("\n\n"))

        for invalid in ("", "two\nlines", "bad\0id"):
            with self.assertRaises(ValueError):
                SSEEvent(event="run_complete", data={}, event_id=invalid)
        with self.assertRaises(ValueError):
            SSEEvent(event="run_complete", data={}, retry_ms=True)
        with self.assertRaises(ValueError):
            SSEEvent(event="unknown", data={})  # type: ignore[arg-type]

    async def test_heartbeat_is_a_comment_and_source_is_closed_on_cancel(self):
        closed = asyncio.Event()

        async def source():
            try:
                await asyncio.Event().wait()
                yield SSEEvent(event="run_complete", data={})
            finally:
                closed.set()

        stream = iter_sse(source(), heartbeat_interval=0.001)
        self.assertEqual(await anext(stream), HEARTBEAT_FRAME)
        await stream.aclose()
        self.assertTrue(closed.is_set())

    async def test_rejects_invalid_heartbeat_interval(self):
        async def source():
            if False:
                yield SSEEvent(event="run_complete", data={})

        for value in (0, -1, float("inf"), float("nan"), True):
            with self.assertRaises(ValueError):
                await anext(iter_sse(source(), heartbeat_interval=value))

    def test_sse_starlette_response_headers_and_event_contract(self):
        async def source():
            yield SSEEvent(
                event="token_stream", data={"token": "hello"}, event_id="run-1:1"
            )
            yield SSEEvent(event="run_error", data={"code": "provider_error"})

        app = FastAPI()
        app.get("/events")(lambda: sse_response(source(), heartbeat_interval=30))
        with TestClient(app).stream("GET", "/events") as response:
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
            self.assertEqual(response.headers["cache-control"], "no-cache, no-transform")
            self.assertEqual(response.headers["x-accel-buffering"], "no")
            body = "".join(response.iter_text())
        self.assertIn("event: token_stream", body)
        self.assertIn("id: run-1:1", body)
        self.assertIn('data: {"token":"hello"}', body)
        self.assertIn("event: run_error", body)
        self.assertIn('data: {"code":"provider_error"}', body)


class SSEClientContractTests(unittest.TestCase):
    def test_incremental_utf8_decode_and_reconnect_cursor(self):
        client = SSEClient(default_retry_ms=2500)
        frame = (
            'id: run-7:9\nretry: 800\nevent: run_error\n'
            'data: {"message":"连接失败：请重试"}\n\n'
        ).encode("utf-8")
        split = frame.index("失败".encode("utf-8")) + 1
        self.assertEqual(client.feed(frame[:split]), [])
        events = client.feed(frame[split:])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event, "run_error")
        self.assertEqual(events[0].data, {"message": "连接失败：请重试"})
        self.assertEqual(client.last_event_id, "run-7:9")
        self.assertEqual(client.retry_ms, 800)
        self.assertEqual(
            client.reconnect_headers(),
            {"Accept": "text/event-stream", "Last-Event-ID": "run-7:9"},
        )

    def test_unknown_event_and_non_object_payload_are_rejected(self):
        client = SSEClient()
        with self.assertRaises(ValueError):
            client.feed(b"event: unsupported\ndata: {}\n\n")
        client = SSEClient()
        with self.assertRaises(ValueError):
            client.feed(b"event: run_complete\ndata: []\n\n")


if __name__ == "__main__":
    unittest.main()
