import unittest

from scripts.benchmark_switch_performance import run_sqlite_baseline


class SwitchPerformanceBenchmarkTests(unittest.TestCase):
    def test_sqlite_baseline_reports_run_and_blackboard_throughput(self):
        result = run_sqlite_baseline(workers=3, operations_per_worker=7)
        self.assertEqual(result["backend"], "legacy_sqlite_local_file")
        self.assertEqual(result["workers"], 3)
        for metric in ("run_lifecycle_transaction", "blackboard_write_read"):
            self.assertEqual(result[metric]["operations"], 21)
            self.assertGreater(result[metric]["ops_per_second"], 0)
            self.assertGreaterEqual(result[metric]["latency_ms"]["p99"], 0)

    def test_sqlite_baseline_is_repeatable_without_shared_database_state(self):
        first = run_sqlite_baseline(workers=1, operations_per_worker=2)
        second = run_sqlite_baseline(workers=1, operations_per_worker=2)
        self.assertEqual(first["run_lifecycle_transaction"]["operations"], 2)
        self.assertEqual(second["blackboard_write_read"]["operations"], 2)


if __name__ == "__main__":
    unittest.main()
