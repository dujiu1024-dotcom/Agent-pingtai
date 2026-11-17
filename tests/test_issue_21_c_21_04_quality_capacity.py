from __future__ import annotations

import unittest

from agent_platform.observability.quality_capacity_baseline import (
    CapacityConfig,
    fixture_fingerprint,
    run_quality_capacity_baseline,
)


class QualityCapacityBaselineTests(unittest.IsolatedAsyncioTestCase):
    async def test_baseline_reports_capacity_cost_and_quality_dimensions(self):
        report = await run_quality_capacity_baseline(CapacityConfig(operations=64, concurrency=8))
        data = report.to_dict()
        self.assertEqual(data["baseline_version"], "c21-04-v1")
        self.assertEqual(data["capacity"]["completed_operations"], 64)
        self.assertLessEqual(data["capacity"]["max_observed_concurrency"], 8)
        self.assertGreater(data["capacity"]["throughput_ops_per_second"], 0)
        self.assertLessEqual(data["capacity"]["latency_p50_ms"], data["capacity"]["latency_p95_ms"])
        self.assertEqual(data["cost"]["estimated_total_usd"], 0.096)
        self.assertEqual(data["quality"]["retrieval_recall_at_k"], 0.5)
        self.assertEqual(data["quality"]["answer_term_f1"], 1.0)
        self.assertEqual(data["quality"]["citation_validity"], 1.0)
        self.assertEqual(data["environment"]["mode"], "synthetic-no-network")
        self.assertEqual(len(report.fixture_sha256), 64)

    async def test_fixture_fingerprint_and_quality_cost_are_repeatable(self):
        first = await run_quality_capacity_baseline(CapacityConfig(operations=8, concurrency=2))
        second = await run_quality_capacity_baseline(CapacityConfig(operations=8, concurrency=2))
        self.assertEqual(fixture_fingerprint(), first.fixture_sha256)
        self.assertEqual(first.fixture_sha256, second.fixture_sha256)
        self.assertEqual(first.quality, second.quality)
        self.assertEqual(first.cost, second.cost)
        self.assertEqual(first.capacity["checksum"], second.capacity["checksum"])

    async def test_invalid_capacity_configuration_fails_closed(self):
        for operations, concurrency in ((0, 1), (10, 0), (10, 257), (True, 1)):
            with self.subTest(operations=operations, concurrency=concurrency):
                with self.assertRaises(ValueError):
                    CapacityConfig(operations=operations, concurrency=concurrency)


if __name__ == "__main__":
    unittest.main()
