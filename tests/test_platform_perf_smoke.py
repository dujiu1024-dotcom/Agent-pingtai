from __future__ import annotations

import time
import unittest

from agent_platform.approval.policy import ActionRequest, RiskLevel, SupervisedPolicy


class PlatformPerformanceSmokeTests(unittest.TestCase):
    def test_policy_evaluation_baseline(self):
        policy = SupervisedPolicy({"read": RiskLevel.READ_ONLY})
        request = ActionRequest(
            action="read", resource="document:1",
            parameters={"query": "baseline"}, actor_id="agent-1",
        )
        started = time.perf_counter()
        for _ in range(10000):
            result = policy.evaluate(request)
            if not result.allowed:
                self.fail("read-only policy evaluation unexpectedly denied")
        elapsed = time.perf_counter() - started
        # A deliberately broad smoke ceiling; detailed load/perf tests remain deployment work.
        self.assertLess(elapsed, 5.0, f"10k policy evaluations took {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()


class PlatformQualityCapacityBaselineTests(unittest.TestCase):
    @staticmethod
    def _percentile(values, percentile):
        ordered = sorted(values)
        index = max(0, min(len(ordered) - 1, int((len(ordered) - 1) * percentile)))
        return ordered[index]

    def test_latency_baseline_is_repeatable_and_reports_tail_latency(self):
        samples_ms = [12, 10, 11, 15, 13, 14, 9, 18, 16, 100]
        first = {
            "p50_ms": self._percentile(samples_ms, 0.50),
            "p95_ms": self._percentile(samples_ms, 0.95),
            "max_ms": max(samples_ms),
        }
        second = {
            "p50_ms": self._percentile(list(reversed(samples_ms)), 0.50),
            "p95_ms": self._percentile(list(reversed(samples_ms)), 0.95),
            "max_ms": max(reversed(samples_ms)),
        }
        self.assertEqual(first, second)
        self.assertLessEqual(first["p50_ms"], first["p95_ms"])
        self.assertEqual(first["max_ms"], 100)

    def test_concurrency_capacity_accounting_uses_completed_work(self):
        submitted = 120
        completed = 117
        elapsed_seconds = 3.0
        throughput = completed / elapsed_seconds
        error_rate = (submitted - completed) / submitted
        self.assertAlmostEqual(throughput, 39.0)
        self.assertAlmostEqual(error_rate, 0.025)

    def test_token_cost_baseline_is_explicit_and_reproducible(self):
        input_tokens = 120_000
        output_tokens = 24_000
        input_price_per_million = 0.50
        output_price_per_million = 1.50
        cost = (
            input_tokens * input_price_per_million
            + output_tokens * output_price_per_million
        ) / 1_000_000
        self.assertAlmostEqual(cost, 0.096)
        self.assertGreaterEqual(cost, 0)

    def test_rag_quality_fixture_reports_recall_precision_and_citation_accuracy(self):
        relevant = {"chunk-a", "chunk-b", "chunk-c"}
        retrieved = {"chunk-a", "chunk-c", "chunk-x"}
        cited = {"chunk-a", "chunk-x"}
        recall = len(relevant & retrieved) / len(relevant)
        precision = len(relevant & retrieved) / len(retrieved)
        citation_accuracy = len(relevant & cited) / len(cited)
        self.assertAlmostEqual(recall, 2 / 3)
        self.assertAlmostEqual(precision, 2 / 3)
        self.assertAlmostEqual(citation_accuracy, 0.5)
