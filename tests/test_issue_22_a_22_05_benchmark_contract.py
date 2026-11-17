from __future__ import annotations

import unittest

from scripts.benchmark_graph_postgres import percentile, summarize


class GraphProductionBenchmarkContractTests(unittest.TestCase):
    def test_percentile_uses_nearest_rank_and_is_order_independent(self):
        self.assertEqual(percentile([9.0, 1.0, 5.0, 3.0, 7.0], 0.50), 5.0)
        self.assertEqual(percentile([9.0, 1.0, 5.0, 3.0, 7.0], 0.95), 9.0)

    def test_summary_reports_latency_distribution(self):
        result = summarize([1.0, 2.0, 3.0, 4.0, 100.0])
        self.assertEqual(result["count"], 5)
        self.assertEqual(result["p50_ms"], 3.0)
        self.assertEqual(result["p95_ms"], 100.0)
        self.assertEqual(result["max_ms"], 100.0)

    def test_empty_samples_and_invalid_percentiles_are_rejected(self):
        with self.assertRaises(ValueError):
            summarize([])
        with self.assertRaises(ValueError):
            percentile([], 0.5)
        with self.assertRaises(ValueError):
            percentile([1.0], 1.1)


if __name__ == "__main__":
    unittest.main()
