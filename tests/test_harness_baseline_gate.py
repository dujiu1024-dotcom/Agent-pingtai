import unittest

from agent_platform.runtime.harness_baseline_gate import HarnessBaseline, evaluate_harness_baseline
from agent_platform.runtime.harness_benchmark import HarnessBenchmarkObservation, HarnessBenchmarkReport


def observation(*, passed=True, duration=100, quality=1.0, cost=0.001, skipped=False):
    return HarnessBenchmarkObservation(
        runtime="candidate", case_id="case", passed=passed, duration_ms=duration,
        finish_reason="completed", usage={"input_tokens": 10}, matched_expectations=("ok",),
        missing_expectations=(), output_text="ok", quality_score=quality,
        estimated_cost_usd=cost, skipped=skipped,
    )


class HarnessBaselineGateTests(unittest.TestCase):
    def test_passes_when_all_explicit_thresholds_are_met(self):
        report = HarnessBenchmarkReport((observation(), observation(duration=200)))
        result = evaluate_harness_baseline(
            report, runtime="candidate",
            baseline=HarnessBaseline(min_eligible_cases=2, max_p95_latency_ms=250,
                                     max_cost_usd_per_case=0.002),
        )
        self.assertTrue(result["passed"])
        self.assertTrue(all(result["checks"].values()))
        self.assertEqual(result["metrics"]["p95_latency_ms"], 200)

    def test_fails_closed_for_missing_runtime_or_missing_pricing(self):
        empty = evaluate_harness_baseline(HarnessBenchmarkReport(()), runtime="candidate")
        self.assertFalse(empty["passed"])
        unpriced = evaluate_harness_baseline(
            HarnessBenchmarkReport((observation(cost=None),)), runtime="candidate",
            baseline=HarnessBaseline(max_cost_usd_per_case=0.01),
        )
        self.assertFalse(unpriced["checks"]["cost"])

    def test_enforces_quality_latency_and_skipped_rate(self):
        report = HarnessBenchmarkReport((
            observation(passed=False, duration=500, quality=0.2),
            observation(skipped=True),
        ))
        result = evaluate_harness_baseline(
            report, runtime="candidate",
            baseline=HarnessBaseline(min_pass_rate=0.9, min_quality_score=0.9,
                                     max_p95_latency_ms=100, max_skipped_rate=0.1),
        )
        self.assertFalse(result["passed"])
        self.assertFalse(result["checks"]["pass_rate"])
        self.assertFalse(result["checks"]["quality"])
        self.assertFalse(result["checks"]["p95_latency"])
        self.assertFalse(result["checks"]["skipped_rate"])

    def test_rejects_invalid_thresholds(self):
        with self.assertRaisesRegex(ValueError, "min_pass_rate"):
            HarnessBaseline(min_pass_rate=1.1)


if __name__ == "__main__":
    unittest.main()
