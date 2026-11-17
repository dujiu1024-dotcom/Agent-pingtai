from __future__ import annotations

import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.observability.prompt_adapter import PromptTemplateHarnessAdapter
from agent_platform.observability.prompts import PromptRegistry
from agent_platform.observability.trace_spans import current_trace_span
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult


class CapturingHarness:
    def __init__(self):
        self.request = None
        self.trace_span = None

    async def run(self, request):
        self.request = request
        self.trace_span = current_trace_span()
        return HarnessResult(output_text="ok")


class PromptTemplateHarnessAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_uses_tenant_scoped_prompt_and_records_version_metadata(self):
        registry = PromptRegistry()
        key = "tenant:8:tenant-a:agent-instructions"
        first = registry.register(key, "Do {task}")
        registry.activate(key, first.version)
        inner = CapturingHarness()
        adapter = PromptTemplateHarnessAdapter(
            inner, registry=registry, prompt_id="agent-instructions",
            variables_resolver=lambda request: {"task": request.input_text},
        )
        request = HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id="run-1", agent_id="agent-a",
                model_template=ModelTemplateRef("general", 1),
            ),
            instructions="stale instruction", input_text="summarize evidence",
        )
        result = await adapter.run(request)
        self.assertEqual(result.output_text, "ok")
        self.assertEqual(inner.request.instructions, "Do summarize evidence")
        self.assertEqual(inner.request.metadata["prompt_id"], "agent-instructions")
        self.assertEqual(inner.request.metadata["prompt_version"], 1)
        self.assertEqual(inner.request.metadata["prompt_content_hash"], first.content_hash)
        self.assertEqual(inner.trace_span.component, "prompt")
        self.assertEqual(inner.trace_span.prompt_id, "agent-instructions")
        self.assertEqual(inner.trace_span.prompt_version, "1")
        self.assertEqual(request.instructions, "stale instruction")

    async def test_variable_mismatch_fails_before_model_execution(self):
        registry = PromptRegistry()
        key = "tenant:8:tenant-a:agent-instructions"
        version = registry.register(key, "Do {task}")
        registry.activate(key, version.version)
        inner = CapturingHarness()
        adapter = PromptTemplateHarnessAdapter(
            inner, registry=registry, prompt_id="agent-instructions",
            variables_resolver=lambda request: {"unexpected": "value"},
        )
        request = HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id="run-2", agent_id="agent-a",
                model_template=ModelTemplateRef("general", 1),
            ),
            instructions="", input_text="",
        )
        with self.assertRaisesRegex(ValueError, "variables mismatch"):
            await adapter.run(request)
        self.assertIsNone(inner.request)


if __name__ == "__main__":
    unittest.main()
