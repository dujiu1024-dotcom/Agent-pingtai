from __future__ import annotations

import asyncio
import unittest

from fastapi.responses import StreamingResponse

from agent_platform.streaming.sse import HEARTBEAT_FRAME, SSEEvent, encode_sse, iter_sse, sse_response


class SSEProtocolTests(unittest.IsolatedAsyncioTestCase):
    def test_encode_event_uses_standard_envelope_and_utf8(self):
        frame = encode_sse(SSEEvent(
            event="token_stream", event_id="run-1:3", retry_ms=2500,
            data={"run_id": "run-1", "sequence": 3, "token": "你好"},
        ))
        self.assertEqual(frame, (
            'id: run-1:3\nretry: 2500\nevent: token_stream\n'
            'data: {"run_id":"run-1","sequence":3,"token":"你好"}\n\n'
        ).encode("utf-8"))

    def test_all_protocol_event_names_are_supported(self):
        for name in ("token_stream", "step_progress", "tool_output", "run_complete", "error"):
            with self.subTest(event=name):
                frame = encode_sse(SSEEvent(event=name, data={"run_id": "r"}))
                self.assertIn(f"event: {name}".encode(), frame)

    def test_rejects_invalid_event_and_event_id_injection(self):
        with self.assertRaises(ValueError):
            SSEEvent(event="unknown", data={})  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            SSEEvent(event="error", event_id="ok\nid: forged", data={})

    def test_rejects_non_json_and_non_finite_payloads(self):
        with self.assertRaises(TypeError):
            encode_sse(SSEEvent(event="error", data={"value": object()}))
        with self.assertRaises(ValueError):
            encode_sse(SSEEvent(event="error", data={"value": float("nan")}))

    async def test_stream_emits_heartbeat_between_events(self):
        async def source():
            yield SSEEvent(event="step_progress", data={"step": "start"})
            await asyncio.sleep(0.03)
            yield SSEEvent(event="run_complete", data={"status": "completed"})

        frames = [frame async for frame in iter_sse(source(), heartbeat_interval=0.005)]
        self.assertIn(HEARTBEAT_FRAME, frames)
        self.assertTrue(any(b"event: step_progress" in frame for frame in frames))
        self.assertTrue(any(b"event: run_complete" in frame for frame in frames))

    async def test_invalid_heartbeat_interval_is_rejected(self):
        async def source():
            if False:
                yield SSEEvent(event="error", data={})
        with self.assertRaises(ValueError):
            async for _ in iter_sse(source(), heartbeat_interval=0):
                pass

    def test_response_sets_sse_and_proxy_safe_headers(self):
        async def source():
            yield SSEEvent(event="run_complete", data={"status": "ok"})
        response = sse_response(source())
        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(response.media_type, "text/event-stream")
        self.assertEqual(response.headers["cache-control"], "no-cache, no-transform")
        self.assertEqual(response.headers["x-accel-buffering"], "no")


if __name__ == "__main__":
    unittest.main()
