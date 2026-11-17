import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.harness_benchmark import (
    HarnessBenchmarkCase,
    HarnessBenchmarkRunner,
)
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult


class FakeAdapter:
    def __init__(self, output):
        self.output = output
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return HarnessResult(
            output_text=self.output,
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="completed",
        )


class HarnessBenchmarkTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_runs_same_cases_against_each_adapter_and_summarizes(self):
        dsh = FakeAdapter("Finding: revenue increased by 12%.")
        strands = FakeAdapter("Finding: revenue increased by 12%.")
        cases = [
            HarnessBenchmarkCase(
                "fact-extraction", "Extract facts.", "Report says revenue increased by 12%.",
                ("revenue increased", "12%"),
            ),
            HarnessBenchmarkCase(
                "evidence-citation", "Cite evidence.", "Evidence ID F-001 supports the claim.",
                ("F-001",),
            ),
        ]
        report = await HarnessBenchmarkRunner(
            adapters={"deepseek-harness": dsh, "strands": strands},
            request_factory=self.request_factory,
        ).run(cases)
        self.assertEqual(len(report.observations), 4)
        self.assertEqual(report.summary()["deepseek-harness"]["cases"], 2)
        self.assertEqual(report.summary()["deepseek-harness"]["pass_rate"], 0.5)
        self.assertEqual(len(dsh.requests), 2)
        self.assertEqual(len(strands.requests), 2)
        self.assertIn("| Runtime | Pass rate (eligible) |", report.to_markdown())
        self.assertEqual(len(report.to_dict()["observations"]), 4)

    async def test_benchmark_never_allows_platform_tools(self):
        adapter = FakeAdapter("ok")

        def unsafe_request_factory(runtime, case):
            request = self.request_factory(runtime, case)
            return HarnessRequest(
                context=request.context,
                instructions=request.instructions,
                input_text=request.input_text,
                allowed_tools=("write_file",),
            )

        runner = HarnessBenchmarkRunner(
            adapters={"strands": adapter}, request_factory=unsafe_request_factory
        )
        with self.assertRaisesRegex(ValueError, "must not expose platform tools"):
            await runner.run([HarnessBenchmarkCase("case-a", "", "input")])

    async def test_adapter_exception_is_recorded_without_aborting_other_cases(self):
        class BrokenAdapter:
            async def run(self, request):
                raise TimeoutError("deadline")

        report = await HarnessBenchmarkRunner(
            adapters={"broken": BrokenAdapter()},
            request_factory=self.request_factory,
        ).run([HarnessBenchmarkCase("case-a", "", "input", ("required",))])
        row = report.observations[0]
        self.assertFalse(row.passed)
        self.assertEqual(row.error_type, "TimeoutError")
        self.assertEqual(row.finish_reason, "error")


if __name__ == "__main__":
    unittest.main()
