from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from agent_platform.models.runtime_router import OpenAICompatibleHTTPTransport
from agent_platform.runtime.execution import LangGraphRunExecutor
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager, RunStatus
from agent_platform.streaming.run_events import RunEventBroker


class _StreamingGraph:
    def __init__(self):
        self.checkpointer = object()
        self.checkpoints = {}

    async def astream_events(self, payload, *, config, version):
        assert version == "v2"
        yield {"event": "on_chain_start", "name": "agent", "data": {}, "parent_ids": []}
        yield {"event": "on_chat_model_start", "name": "chat", "data": {}}
        yield {"event": "on_chat_model_stream", "name": "chat",
               "data": {"chunk": SimpleNamespace(content="Hello")}}
        yield {"event": "on_chat_model_end", "name": "chat", "data": {}}
        yield {"event": "on_tool_start", "name": "search", "data": {"input": {"q": "x"}}}
        yield {"event": "on_tool_end", "name": "search", "data": {"output": {"hits": 1}}}
        self.checkpoints[config["configurable"]["thread_id"]] = "cp-stream"
        yield {"event": "on_chain_end", "name": "agent",
               "data": {"output": {"answer": "Hello"}}, "parent_ids": []}

    async def aget_state(self, config):
        checkpoint = self.checkpoints.get(config["configurable"]["thread_id"])
        return SimpleNamespace(config={"configurable": {"checkpoint_id": checkpoint}})


class _FakeResponse:
    def __init__(self, lines):
        self.lines = iter(lines)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def readline(self):
        return next(self.lines, b"")


class _FakeOpener:
    def __init__(self, response):
        self.response = response
        self.request = None

    def open(self, request, timeout):
        self.request = request
        return self.response


class AgentStreamingTests(unittest.IsolatedAsyncioTestCase):
    async def test_executor_emits_token_step_tool_and_terminal_events(self):
        store = InMemoryRunStateStore()
        lifecycle = RunLifecycleManager(store)
        broker = RunEventBroker()
        graph = _StreamingGraph()
        await store.create("tenant", "run-stream")
        executor = LangGraphRunExecutor(
            graph=graph, lifecycle=lifecycle, timeout_seconds=1, event_broker=broker,
        )
        result = await executor.execute(
            tenant_id="tenant", run_id="run-stream", input={"prompt": "hello"},
        )
        self.assertEqual(result, {"answer": "Hello"})
        self.assertEqual((await store.get("tenant", "run-stream")).status, RunStatus.SUCCEEDED)

        stream = await broker.subscribe(tenant_id="tenant", run_id="run-stream")
        events = []
        async for event in stream:
            events.append(event)
        event_types = [event.data["event_type"] for event in events]
        self.assertEqual(event_types[0], "RUN_START")
        self.assertIn("LLM_REQUEST", event_types)
        self.assertIn("LLM_RESPONSE", event_types)
        self.assertIn("STEP_START", event_types)
        self.assertIn("STEP_FINISH", event_types)
        self.assertIn("TOOL_CALL", event_types)
        self.assertIn("TOOL_OUTPUT", event_types)
        token = next(event for event in events if event.data["event_type"] == "TOKEN_STREAM")
        self.assertEqual(token.data["token"], "Hello")
        self.assertEqual(event_types[-1], "COMPLETE")

    async def test_openai_transport_streams_tokens_and_requests_usage(self):
        lines = [
            b'data: {"choices":[{"delta":{"content":"Hi"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"!"}}]}\n',
            b'data: {"choices":[],"usage":{"prompt_tokens":2,"completion_tokens":2}}\n',
            b"data: [DONE]\n",
        ]
        transport = OpenAICompatibleHTTPTransport()
        opener = _FakeOpener(_FakeResponse(lines))
        transport._opener = opener
        config = SimpleNamespace(
            base_url="https://provider.example/v1", api_key="not-a-real-secret",
            model_id="model", parameters={},
        )
        tokens = []

        async def on_token(token):
            tokens.append(token)

        response = await transport.complete(
            config=config, messages=({"role": "user", "content": "hello"},),
            request_parameters={}, timeout_seconds=1, on_token=on_token,
        )
        request_body = json.loads(opener.request.data)
        self.assertIs(request_body["stream"], True)
        self.assertEqual(request_body["stream_options"], {"include_usage": True})
        self.assertEqual(tokens, ["Hi", "!"])
        self.assertEqual(response["choices"][0]["message"]["content"], "Hi!")
        self.assertEqual(response["usage"]["completion_tokens"], 2)

    async def test_run_scoped_model_client_forwards_token_and_model_lifecycle_callbacks(self):
        from agent_platform.models.runtime_router import RoutedModelResponse
        from agent_platform.runtime.registry_runtime import RunScopedModelClient
        from agent_platform.streaming.context import run_event_context, token_callback_context

        class SnapshotService:
            async def get(self, *, tenant_id, run_id):
                return SimpleNamespace(tenant_id=tenant_id, run_id=run_id)

        class Router:
            async def complete(self, **kwargs):
                self.kwargs = kwargs
                await kwargs["on_token"]("delta")
                return RoutedModelResponse(
                    content="delta", model_id="model", template_id="template",
                    template_version=1, provider_id="provider", provider_version=1,
                    usage={"prompt_tokens": 1, "completion_tokens": 1}, fallback_used=False,
                )

        router = Router()
        client = RunScopedModelClient(
            tenant_id="tenant", run_id="run", template_id="template",
            snapshots=SnapshotService(), router=router,
        )
        tokens = []
        events = []

        async def on_token(token):
            tokens.append(token)

        async def on_event(event_type, **data):
            events.append((event_type, data))

        with token_callback_context(on_token), run_event_context(on_event):
            result = await client.complete(
                messages=({"role": "user", "content": "hello"},), request_id="req",
            )
        self.assertEqual(tokens, ["delta"])
        self.assertEqual(result.content, "delta")
        self.assertEqual(
            [event_type for event_type, _data in events],
            ["LLM_REQUEST", "LLM_RESPONSE"],
        )


if __name__ == "__main__":
    unittest.main()
