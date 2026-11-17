from __future__ import annotations

import asyncio
import unittest
from decimal import Decimal

from agent_platform.runtime.quality_capacity_baseline import (
    BenchmarkCase,
    RegressionPolicy,
    WorkloadResult,
    compare_quality_capacity,
    dataset_fingerprint,
    run_quality_capacity_benchmark,
)


CASES = (
    BenchmarkCase("answer-1", "What is the status?", ("ready",)),
    BenchmarkCase("rag-1", "Find the approved policy.", ("approved",), expected_document_ids=("doc-a",)),
    BenchmarkCase("rag-2", "Find the deployment note.", ("deployed",), expected_document_ids=("doc-b",)),
)


class QualityCapacityBaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixed_suite_is_repeatable_and_reports_performance_cost_and_rag(self):
        active = 0
        peak = 0

        async def workload(case):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.001)
            active -= 1
            expected = {
                "answer-1": ("ready", ()),
                "rag-1": ("approved", ("doc-a",)),
                "rag-2": ("deployed", ("doc-b",)),
            }[case.case_id]
            return WorkloadResult(expected[0], 100, 50, expected[1])

        report = await run_quality_capacity_benchmark(
            cases=CASES, workload=workload, iterations=2, concurrency=2,
            input_price_per_million=Decimal("1.00"),
            output_price_per_million=Decimal("2.00"),
        )
        self.assertEqual(report.completed, 6)
        self.assertEqual(report.failed, 0)
        self.assertLessEqual(peak, 2)
        self.assertEqual(report.pass_rate, 1.0)
        self.assertEqual(report.recall_at_k, 1.0)
        self.assertEqual(report.mean_reciprocal_rank, 1.0)
        self.assertEqual(report.total_input_tokens, 600)
        self.assertEqual(report.total_output_tokens, 300)
        self.assertEqual(report.estimated_cost_usd, "0.001200")
        self.assertEqual(len(report.dataset_sha256), 64)
        self.assertGreaterEqual(report.latency_p95_ms, report.latency_p50_ms)
        self.assertGreater(report.throughput_per_second, 0)

    async def test_failure_is_counted_and_does_not_abort_the_suite(self):
        async def workload(case):
            if case.case_id == "rag-1":
                raise TimeoutError("synthetic model timeout")
            return WorkloadResult("ready approved deployed", 1, 1, ("doc-a", "doc-b"))

        report = await run_quality_capacity_benchmark(
            cases=CASES, workload=workload, iterations=1, concurrency=3,
        )
        self.assertEqual(report.completed, 2)
        self.assertEqual(report.failed, 1)
        self.assertLess(report.pass_rate, 1.0)

    async def test_dataset_fingerprint_changes_with_version_or_content(self):
        self.assertEqual(dataset_fingerprint(CASES, suite_version=1), dataset_fingerprint(CASES, suite_version=1))
        self.assertNotEqual(dataset_fingerprint(CASES, suite_version=1), dataset_fingerprint(CASES, suite_version=2))
        changed = (BenchmarkCase("answer-1", "Changed prompt", ("ready",)), *CASES[1:])
        self.assertNotEqual(dataset_fingerprint(CASES, suite_version=1), dataset_fingerprint(changed, suite_version=1))

    async def test_regression_gate_detects_latency_cost_and_quality_drop(self):
        async def good(_case):
            return WorkloadResult("ready approved deployed", 10, 10, ("doc-a", "doc-b"))

        baseline = await run_quality_capacity_benchmark(
            cases=CASES, workload=good, iterations=1, concurrency=1,
            input_price_per_million=Decimal("1"), output_price_per_million=Decimal("1"),
        )

        async def bad(_case):
            await asyncio.sleep(0.01)
            return WorkloadResult("wrong answer", 100, 100, ())

        candidate = await run_quality_capacity_benchmark(
            cases=CASES, workload=bad, iterations=1, concurrency=1,
            input_price_per_million=Decimal("2"), output_price_per_million=Decimal("2"),
        )
        passed, failures = compare_quality_capacity(
            candidate, baseline,
            RegressionPolicy(max_latency_p95_ratio=1.01, max_cost_ratio=1.01),
        )
        self.assertFalse(passed)
        self.assertIn("p95_latency_regression", failures)
        self.assertIn("estimated_cost_regression", failures)
        self.assertIn("answer_quality_regression", failures)
        self.assertIn("rag_recall_regression", failures)

    async def test_invalid_inputs_fail_closed(self):
        async def workload(_case):
            return WorkloadResult("ready", -1, 1)

        report = await run_quality_capacity_benchmark(cases=CASES, workload=workload, iterations=1)
        self.assertEqual(report.failed, len(CASES))
        with self.assertRaisesRegex(ValueError, "unique"):
            await run_quality_capacity_benchmark(
                cases=(CASES[0], CASES[0]), workload=workload, iterations=1,
            )


if ___name__ == "__main__":
    unittest.main()
