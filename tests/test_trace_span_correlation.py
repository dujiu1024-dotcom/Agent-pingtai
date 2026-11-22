from __future__ import annotations

import unittest
from contextlib import contextmanager
from types import SimpleNamespace

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.models.registry_resolver import RegistryModelResolver
from agent_platform.models.runtime_router import OpenAICompatibleModelRouter
from agent_platform.observability.harness import ObservedHarnessAdapter
from agent_platform.observability.trace_context import TraceContext, TraceContextScope
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult


class Observer:
    def __init__(self):
        self.metadata = []

    @contextmanager
    def observe(self, *, name, input, metadata):
        self.metadata.append(dict(metadata))
        yield SimpleNamespace(update=lambda **kwargs: None)


class Harness:
    async def run(self, request):
        return HarnessResult(output_text="ok", usage={"input_tokens": 1, "output_tokens": 1})


class Secrets:
    async def resolve(self, secret_ref):
        return "test-secret"


class ModelTransport:
    async def complete(self, *, config, messages, request_parameters, timeout_seconds, tools=None, tool_choice=None, on_token=None):
        return {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }


class AuditRecorder:
    async def record(self, **event):
        return None


class TraceSpanCorrelationTests(unittest.IsolatedAsyncioTestCase):
    async def test_harness_observation_inherits_request_trace_context(self):
        observer = Observer()
        request = HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id="run-a", agent_id="agent-a",
                model_template=ModelTemplateRef("general", 1),
                correlation_id="context-correlation",
            ),
            instructions="do work", input_text="hello",
        )
        context = TraceContext(
            "1234567890abcdef1234567890abcdef",
            "1234567890abcdef",
            correlation_id="http-request-7",
        )
        adapter = ObservedHarnessAdapter(Harness(), observer=observer)
        with TraceContextScope(context):
            await adapter.run(request)
        metadata = observer.metadata[0]
        self.assertEqual(metadata["trace_id"], context.trace_id)
        self.assertNotEqual(metadata["span_id"], context.span_id)
        self.assertEqual(metadata["parent_span_id"], context.span_id)
        self.assertEqual(metadata["traceparent"], context.traceparent)
        self.assertEqual(metadata["correlation_id"], "http-request-7")

    async def test_model_attempt_observation_inherits_request_trace_context(self):
        observer = Observer()
        snapshot = SimpleNamespace(tenant_id="tenant-a", entries=(
            {
                "kind": "model_template", "item_id": "primary", "version": 1,
                "definition": {
                    "provider_id": "provider-a", "provider_version": 1,
                    "model_id": "model-a", "parameters": {},
                    "capabilities": ["text"], "allowed_data_levels": ["internal"],
                },
            },
            {
                "kind": "model_provider", "item_id": "provider-a", "version": 1,
                "definition": {
                    "provider_type": "openai-compatible",
                    "base_url": "https://provider.example/v1",
                    "secret_ref": "secret://tenant-a/provider-a",
                },
            },
        ))
        router = OpenAICompatibleModelRouter(
            audit_sink=AuditRecorder(),
            resolver=RegistryModelResolver(Secrets()),
            transport=ModelTransport(),
            observer=observer,
        )
        context = TraceContext(
            "abcdefabcdefabcdefabcdefabcdefab",
            "abcdefabcdefabcd",
            correlation_id="http-request-8",
        )
        with TraceContextScope(context):
            await router.complete(
                snapshot=snapshot,
                tenant_id="tenant-a",
                run_id="run-a",
                template_id="primary",
                messages=({"role": "user", "content": "hello"},),
                request_id="request-a",
            )
        metadata = observer.metadata[0]
        self.assertEqual(metadata["trace_id"], context.trace_id)
        self.assertEqual(metadata["traceparent"], context.traceparent)
        self.assertEqual(metadata["correlation_id"], "http-request-8")


if __name__ == "__main__":
    unittest.main()
