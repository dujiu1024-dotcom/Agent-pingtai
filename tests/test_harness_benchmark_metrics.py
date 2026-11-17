from __future__ import annotations

import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.harness_benchmark import (
    HarnessBenchmarkCase,
    HarnessBenchmarkRunner,
    HarnessTokenPricing,
)
from agent_platform.runtime.protocols import (
    HarnessCapabilities,
    HarnessRequest,
    HarnessResult,
    HarnessStreamEvent,
    ToolCall,
)


class BenchmarkAdapter:
    def __init__(self, output="alpha evidence", usage=None, fail_first=False):
        self.output = output
        self.usage = usage or {"input_tokens": 1000, "output_tokens": 500}
        self.fail_first = fail_first
        self.calls = 0

    async def run(self, request):
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("transient provider failure")
        return HarnessResult(output_text=self.output, usage=self.usage)


class HarnessBenchmarkMetricsTests(unittest.IsolatedAsyncioTestCase):
    def request_factory(self, runtime, case):
        return HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a",
                run_id=f"{case.case_id}-{runtime}",
                agent_id="benchmark-agent",
                model_template=ModelTemplateRef("general", 1),
            ),
            instructions=case.instructions,
            input_text=case.input_text,
        )

    async def test_reports_quality_and_estimated_token_cost(self):
        adapter = BenchmarkAdapter()
        report = await HarnessBenchmarkRunner(
            adapters={"strands": adapter},
            request_factory=self.request_factory,
            pricing={
                "strands": HarnessTokenPricing(
                    input_usd_per_million=1.0,
                    output_usd_per_million=2.0,
                )
            },
        ).run([
            HarnessBenchmarkCase(
                "quality-cost", "analyze", "same task",
                expected_contains=("alpha", "evidence"),
            )
        ])
        row = report.observations[0]
        self.assertTrue(row.passed)
        self.assertEqual(row.quality_score, 1.0)
        self.assertEqual(row.estimated_cost_usd, 0.002)
        summary = report.summary()["strands"]
        self.assertEqual(summary["average_quality_score"], 1.0)
        self.assertEqual(summary["estimated_cost_usd"], 0.002)
        self.assertIn("Est. cost (USD)", report.to_markdown())

    async def test_retries_transient_failure_and_marks_recovery(self):
        adapter = BenchmarkAdapter(fail_first=True)
        report = await HarnessBenchmarkRunner(
            adapters={"deepseek": adapter},
            request_factory=self.request_factory,
            recovery_attempts=1,
        ).run([HarnessBenchmarkCase("recover", "analyze", "same task")])
        row = report.observations[0]
        self.assertTrue(row.passed)
        self.assertEqual(row.attempts, 2)
        self.assertTrue(row.recovered)
        self.assertEqual(adapter.calls, 2)

    async def test_gateway_tools_require_explicit_gateway_verifier(self):
        def tool_request_factory(runtime, case):
            base = self.request_factory(runtime, case)
            return HarnessRequest(
                context=base.context,
                instructions=base.instructions,
                input_text=base.input_text,
                allowed_tools=("knowledge.search",),
            )

        adapter = BenchmarkAdapter()
        runner = HarnessBenchmarkRunner(
            adapters={"strands": adapter},
            request_factory=tool_request_factory,
        )
        with self.assertRaisesRegex(ValueError, "Gateway-only verifier"):
            await runner.run([HarnessBenchmarkCase("tools", "analyze", "same task")])

        verified = HarnessBenchmarkRunner(
            adapters={"strands": adapter},
            request_factory=tool_request_factory,
            gateway_tool_verifier=lambda runtime, request: (
                runtime == "strands" and request.allowed_tools == ("knowledge.search",)
            ),
        )
        report = await verified.run([
            HarnessBenchmarkCase("tools-verified", "analyze", "same task")
        ])
        self.assertTrue(report.observations[0].passed)

    async def test_benchmark_measures_streaming_first_token_and_events(self):
        class StreamingAdapter:
            capabilities = HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text"}),
                streaming=True,
            )

            async def run(self, request):
                raise AssertionError("streaming case must use stream()")

            async def stream(self, request):
                yield HarnessStreamEvent(event_type="text_delta", text_delta="alpha ")
                yield HarnessStreamEvent(event_type="text_delta", text_delta="evidence")
                yield HarnessStreamEvent(
                    event_type="completed",
                    result=HarnessResult(
                        output_text="alpha evidence",
                        usage={"input_tokens": 10, "output_tokens": 5},
                    ),
                )

        report = await HarnessBenchmarkRunner(
            adapters={"strands-stream": StreamingAdapter()},
            request_factory=self.request_factory,
        ).run([
            HarnessBenchmarkCase(
                "streaming-case", "analyze", "same task",
                expected_contains=("alpha", "evidence"),
                streaming=True,
            )
        ])
        row = report.observations[0]
        self.assertTrue(row.passed)
        self.assertIsNotNone(row.time_to_first_token_ms)
        self.assertEqual(row.streamed_events, 3)
        self.assertEqual(report.summary()["strands-stream"]["streamed_events"], 3)
        self.assertIn("TTFT (ms)", report.to_markdown())

    async def test_streaming_case_rejects_adapter_without_declared_streaming(self):
        runner = HarnessBenchmarkRunner(
            adapters={"text-only": BenchmarkAdapter()},
            request_factory=self.request_factory,
        )
        with self.assertRaisesRegex(ValueError, "does not declare Harness streaming"):
            await runner.run([
                HarnessBenchmarkCase(
                    "streaming-unsupported", "analyze", "same task", streaming=True
                )
            ])

    async def test_quality_threshold_and_pricing_validation(self):
        report = await HarnessBenchmarkRunner(
            adapters={"model": BenchmarkAdapter(output="alpha only")},
            request_factory=self.request_factory,
        ).run([
            HarnessBenchmarkCase(
                "quality-threshold", "analyze", "same task",
                expected_contains=("alpha", "beta"),
                quality_threshold=0.75,
            )
        ])
        self.assertFalse(report.observations[0].passed)
        self.assertEqual(report.observations[0].quality_score, 0.5)
        with self.assertRaisesRegex(ValueError, "finite and non-negative"):
            HarnessTokenPricing(input_usd_per_million=-1, output_usd_per_million=0)

    async def test_reports_actual_gateway_tool_calls(self):
        class ToolAdapter(BenchmarkAdapter):
            async def run(self, request):
                return HarnessResult(
                    output_text="benchmark-tool-result",
                    tool_calls=(ToolCall("knowledge.search", {"query": "benchmark-probe"}, "call-1"),),
                )

        def tool_request_factory(runtime, case):
            base = self.request_factory(runtime, case)
            return HarnessRequest(
                context=base.context, instructions=base.instructions,
                input_text=base.input_text, allowed_tools=("knowledge.search",),
            )

        report = await HarnessBenchmarkRunner(
            adapters={"gateway": ToolAdapter()},
            request_factory=tool_request_factory,
            gateway_tool_verifier=lambda runtime, request: request.allowed_tools == ("knowledge.search",),
        ).run([HarnessBenchmarkCase(
            "gateway-tool", "call tool", "input", ("benchmark-tool-result",),
            required_tools=("knowledge.search",),
        )])
        row = report.observations[0]
        self.assertTrue(row.passed)
        self.assertEqual(row.tool_calls_count, 1)
        self.assertEqual(report.summary()["gateway"]["tool_calls"], 1)


if __name__ == "__main__":
    unittest.main()
