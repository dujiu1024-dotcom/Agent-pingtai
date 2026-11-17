from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from agent_platform.models.runtime_router import OpenAICompatibleHTTPTransport


class _Response:
    def __init__(self, *, body: bytes | None = None, lines: list[bytes] | None = None):
        self.body = body
        self.lines = iter(lines or [])

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, size: int = -1) -> bytes:
        if self.body is None:
            return b""
        return self.body[:size]

    def readline(self, _size: int = -1) -> bytes:
        return next(self.lines, b"")


class _ParityOpener:
    def __init__(self, complete_content: str, usage: dict[str, int]):
        self.complete_content = complete_content
        self.usage = usage
        self.requests: list[dict] = []

    def open(self, request, timeout):
        payload = json.loads(request.data)
        self.requests.append({"payload": payload, "accept": request.get_header("Accept"), "timeout": timeout})
        if payload.get("stream") is True:
            chunks = [
                {"choices": [{"delta": {"content": "流式 "}}]},
                {"choices": [{"delta": {"content": "输出🙂"}}]},
                {"choices": [], "usage": self.usage},
            ]
            lines = [*(b"data: " + json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n" for item in chunks), b"data: [DONE]\n"]
            return _Response(lines=lines)
        return _Response(body=json.dumps({
            "choices": [{"message": {"content": self.complete_content}}],
            "usage": self.usage,
        }, ensure_ascii=False).encode("utf-8"))


class StreamingParityAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_streaming_and_non_streaming_return_identical_complete_content(self):
        expected = "流式 输出🙂"
        usage = {"prompt_tokens": 8, "completion_tokens": 3, "total_tokens": 11}
        transport = OpenAICompatibleHTTPTransport()
        opener = _ParityOpener(expected, usage)
        transport._opener = opener
        config = SimpleNamespace(
            base_url="https://provider.example/v1",
            api_key="test-only-secret",
            model_id="pinned-model",
            parameters={},
        )
        messages = ({"role": "user", "content": "same input"},)

        complete = await transport.complete(
            config=config, messages=messages, request_parameters={}, timeout_seconds=2,
        )
        deltas: list[str] = []

        async def on_token(token: str) -> None:
            deltas.append(token)

        streamed = await transport.complete(
            config=config, messages=messages, request_parameters={},
            timeout_seconds=2, on_token=on_token,
        )

        self.assertEqual(complete["choices"][0]["message"]["content"], expected)
        self.assertEqual(streamed["choices"][0]["message"]["content"], expected)
        self.assertEqual("".join(deltas), expected)
        self.assertEqual(complete["usage"], streamed["usage"])
        self.assertEqual([item["payload"].get("stream", False) for item in opener.requests], [False, True])
        self.assertEqual(opener.requests[0]["accept"], "application/json")
        self.assertEqual(opener.requests[1]["accept"], "text/event-stream")
        self.assertEqual(opener.requests[0]["payload"]["messages"], opener.requests[1]["payload"]["messages"])
        self.assertEqual(opener.requests[0]["payload"]["model"], opener.requests[1]["payload"]["model"])


if __name__ == "__main__":
    unittest.main()
