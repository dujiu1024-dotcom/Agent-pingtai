from __future__ import annotations

import unittest

from agent_platform.observability.trace_context import TraceContext, TraceContextScope
from agent_platform.observability.trace_spans import trace_span


class TraceSpanContractMatrixTests(unittest.TestCase):
    def test_request_prompt_tool_retrieval_model_spans_share_trace_and_parentage(self):
        request_context = TraceContext(
            "1234567890abcdef1234567890abcdef",
            "1234567890abcdef",
            correlation_id="http-correlation-16",
        )
        exported = []

        with TraceContextScope(request_context):
            with trace_span(
                "http.request",
                component="request",
                tenant_id="tenant-16",
                request_id="request-16",
                correlation_id="http-correlation-16",
                exporter=exported.append,
            ) as request:
                with trace_span(
                    "prompt.render",
                    component="prompt",
                    tenant_id="tenant-16",
                    run_id="run-16",
                    agent_id="agent-16",
                    prompt_id="support",
                    prompt_version="v4",
                    attributes={"api_key": "sk-sensitive-value"},
                    exporter=exported.append,
                ) as prompt:
                    with trace_span(
                        "tool.invoke",
                        component="tool",
                        tenant_id="tenant-16",
                        run_id="run-16",
                        agent_id="agent-16",
                        tool_name="search",
                        exporter=exported.append,
                    ) as tool:
                        with trace_span(
                            "knowledge.retrieve",
                            component="retrieval",
                            tenant_id="tenant-16",
                            run_id="run-16",
                            knowledge_collection_id="kb-16",
                            retrieval_id="retrieval-16",
                            exporter=exported.append,
                        ) as retrieval:
                            with trace_span(
                                "model.generate",
                                component="model",
                                tenant_id="tenant-16",
                                run_id="run-16",
                                agent_id="agent-16",
                                model_id="model-16",
                                exporter=exported.append,
                            ) as model:
                                self.assertEqual(model.trace_id, request_context.trace_id)
                                self.assertEqual(model.parent_span_id, retrieval.span_id)

                spans = (request, prompt, tool, retrieval, model)
                for parent, child in zip(spans, spans[1:]):
                    self.assertEqual(child.trace_id, parent.trace_id)
                    self.assertEqual(child.parent_span_id, parent.span_id)
                self.assertTrue(all(span.tenant_id == "tenant-16" for span in spans))
                self.assertEqual(request.parent_span_id, request_context.span_id)
                self.assertEqual(request.correlation_id, "http-correlation-16")
                self.assertEqual(prompt.prompt_version, "v4")
                self.assertEqual(tool.tool_name, "search")
                self.assertEqual(retrieval.knowledge_collection_id, "kb-16")
                self.assertEqual(retrieval.retrieval_id, "retrieval-16")
                self.assertEqual(model.model_id, "model-16")

        self.assertEqual(len(exported), 5)
        prompt_export = next(item for item in exported if item["component"] == "prompt")
        self.assertEqual(prompt_export["attributes"]["api_key"], "[REDACTED]")
        self.assertTrue(all(item["status"] == "ok" for item in exported))


if __name__ == "__main__":
    unittest.main()
