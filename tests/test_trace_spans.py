from __future__ import annotations

import asyncio
import unittest

from agent_platform.observability.trace_context import TraceContext, TraceContextScope
from agent_platform.observability.trace_spans import (
    current_trace_span, trace_span,
)


class CrossComponentTraceSpanTests(unittest.TestCase):
    def test_span_carries_all_component_dimensions_and_redacts_sensitive_values(self):
        exported = []
        request = TraceContext(
            "1234567890abcdef1234567890abcdef", "1234567890abcdef",
            correlation_id="http-request-9",
        )
        with TraceContextScope(request):
            with trace_span(
                "rag.search", component="retrieval", tenant_id="tenant-a",
                request_id="request-9", run_id="run-9", agent_id="agent-9",
                model_id="model-9", prompt_id="prompt-9", prompt_version="v3",
                tool_name="vector.search", knowledge_collection_id="kb-9",
                retrieval_id="retrieval-9",
                attributes={"authorization": "Bearer secret-token", "query_chars": 12},
                exporter=exported.append,
            ) as parent:
                self.assertEqual(parent.trace_id, request.trace_id)
                self.assertEqual(parent.parent_span_id, request.span_id)
                self.assertEqual(parent.correlation_id, "http-request-9")
                with trace_span("tool.invoke", component="tool", tenant_id="tenant-a") as child:
                    self.assertEqual(child.trace_id, parent.trace_id)
                    self.assertEqual(child.parent_span_id, parent.span_id)
                    self.assertEqual(current_trace_span(), child)
                self.assertEqual(current_trace_span(), parent)
        self.assertIsNone(current_trace_span())
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["status"], "ok")
        self.assertEqual(exported[0]["attributes"]["authorization"], "[REDACTED]")
        self.assertEqual(exported[0]["knowledge_collection_id"], "kb-9")
        self.assertGreaterEqual(exported[0]["duration_ms"], 0)

    def test_exception_marks_span_error_and_exporter_failure_is_isolated(self):
        exported = []
        with self.assertRaisesRegex(RuntimeError, "component failed"):
            with trace_span("model.complete", component="model", exporter=exported.append):
                raise RuntimeError("component failed")
        self.assertEqual(exported[0]["status"], "error")
        with trace_span("agent.run", component="agent", exporter=lambda _: 1 / 0):
            pass
        self.assertIsNone(current_trace_span())

    def test_rejects_invalid_component_and_identifier(self):
        with self.assertRaises(ValueError):
            trace_span("x", component="unknown")
        with self.assertRaises(ValueError):
            trace_span("x", component="run", tenant_id="bad\nvalue")

    def test_async_tasks_inherit_parent_and_restore_context(self):
        async def child():
            return current_trace_span()

        async def scenario():
            with trace_span("agent.run", component="agent") as parent:
                inherited = await asyncio.create_task(child())
                self.assertIs(inherited, parent)
            self.assertIsNone(current_trace_span())

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
