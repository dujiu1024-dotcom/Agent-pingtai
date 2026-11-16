from __future__ import annotations

import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.harness_benchmark import (
    HarnessBenchmarkCase,
    HarnessBenchmarkRunner,
    HarnessTokenPricing,
)
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult


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


if __name__ == "__main__":
    unittest.main()
