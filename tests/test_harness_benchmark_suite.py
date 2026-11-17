import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.harness_benchmark_suite import (
    FIXED_HARNESS_CASES,
    benchmark_evidence_payload,
    candidate_scorecards,
    run_fixed_harness_benchmark,
)
from agent_platform.runtime.protocols import (
    HarnessCapabilities,
    HarnessRequest,
    HarnessResult,
)


class CandidateAdapter:
    def __init__(self, *, streaming=False):
        self.capabilities = HarnessCapabilities(
            streaming=streaming,
            cancellation=False,
            timeout=True,
            resume=False,
            tool_gateway=False,
        )
        self.calls = 0

    async def run(self, request):
        self.calls += 1
        return HarnessResult(
            output_text="revenue 12% E-104 25 timeout ORBIT-27 completed",
            usage={"input_tokens": 20, "output_tokens": 8},
            finish_reason="completed",
        )

    async def stream(self, request):
        from agent_platform.runtime.protocols import HarnessStreamEvent

        self.calls += 1
        yield HarnessStreamEvent(event_type="text_delta", text_delta="completed")
        yield HarnessStreamEvent(
            event_type="completed",
            result=HarnessResult(
                output_text="completed",
                usage={"input_tokens": 20, "output_tokens": 8},
                finish_reason="completed",
            ),
        )


class HarnessBenchmarkSuiteTests(unittest.IsolatedAsyncioTestCase):
    def request_factory(self, runtime, case):
        return HarnessRequest(
            context=RunContext(
                tenant_id="benchmark",
                run_id=f"{runtime}-{case.case_id}",
                agent_id="harness-benchmark",
                model_template=ModelTemplateRef("benchmark-model", 1),
            ),
            instructions=case.instructions,
            input_text=case.input_text,
        )

    def test_fixed_dataset_is_stable_and_covers_streaming(self):
        self.assertEqual(
            tuple(case.case_id for case in FIXED_HARNESS_CASES),
            (
                "fact-extraction",
                "evidence-grounding",
                "structured-reasoning",
                "instruction-following",
                "long-context-recall",
                "streaming-response",
                "gateway-tool-call",
            ),
        )
        streaming_case = next(case for case in FIXED_HARNESS_CASES if case.case_id == "streaming-response")
        self.assertTrue(streaming_case.streaming)

    def test_candidate_requires_declared_capabilities(self):
        with self.assertRaisesRegex(ValueError, "must declare Harness capabilities"):
            candidate_scorecards({"missing": object()})

    async def test_comparison_skips_unsupported_streaming_and_emits_capability_evidence(self):
        no_stream = CandidateAdapter()
        streaming = CandidateAdapter(streaming=True)
        scorecards, report = await run_fixed_harness_benchmark(
            adapters={"candidate-a": no_stream, "candidate-b": streaming},
            request_factory=self.request_factory,
        )
        self.assertEqual(len(scorecards), 2)
        self.assertEqual(len([row for row in report.observations if row.runtime == "candidate-a"]), 7)
        self.assertEqual(len([row for row in report.observations if row.runtime == "candidate-b"]), 7)
        summary_a = payload["summary"]["candidate-a"]
        summary_b = payload["summary"]["candidate-b"]
        self.assertEqual((summary_a["eligible_cases"], summary_a["skipped_cases"]), (5, 2))
        self.assertEqual((summary_b["eligible_cases"], summary_b["skipped_cases"]), (6, 1))
        self.assertEqual(summary_a["pass_rate"], 1.0)
        payload = benchmark_evidence_payload(scorecards, report)
        self.assertEqual(payload["suite_version"], 1)
        self.assertEqual(len(payload["candidate_capabilities"]), 2)
        self.assertTrue(any("Cancellation" in item for item in payload["limitations"]))
        self.assertEqual(payload["summary"]["candidate-b"]["passed"], 6)
        self.assertEqual(payload["summary"]["candidate-b"]["cases"], 7)

    async def test_runs_explicit_cancellation_probe_and_records_evidence(self):
        candidate = CandidateAdapter()
        async def probe(adapter):
            self.assertIs(adapter, candidate)
            return True
        scorecards, report = await run_fixed_harness_benchmark(
            adapters={"candidate": candidate},
            request_factory=self.request_factory,
            cancellation_probes={"candidate": probe},
        )
        self.assertTrue(scorecards[0].cancellation_probe_passed)
        payload = benchmark_evidence_payload(scorecards, report)
        self.assertTrue(payload["candidate_capabilities"][0]["cancellation_probe_passed"])


if __name__ == "__main__":
    unittest.main()
