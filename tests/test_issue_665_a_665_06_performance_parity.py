from __future__ import annotations

import unittest

from scripts.evaluate_switch_performance_parity import evaluate_parity


def report(*, run=100.0, blackboard=200.0, redis=5.0, pool=2.0):
    return {
        "workload": {
            "workers": 8, "operations_per_worker": 100,
            "dataset_size": 10000, "warmup_operations": 100,
            "measurement_seconds": 30,
        },
        "environment": {
            "cpu_model": "CI reference CPU", "cpu_count": 4,
            "memory_mb": 8192, "os": "Linux",
            "python_version": "3.12", "storage_profile": "local SSD",
        },
        "metrics": {
            "run_throughput_ops_per_second": run,
            "blackboard_qps": blackboard,
            "redis_queue_latency_p95_ms": redis,
            "postgres_pool_wait_p95_ms": pool,
        },
    }


class SwitchPerformanceParityTests(unittest.TestCase):
    def test_passes_when_all_platform_metrics_are_not_worse(self):
        result = evaluate_parity(
            report(), report(run=101, blackboard=205, redis=4.9, pool=1.9)
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["failures"], [])

    def test_fails_if_any_metric_regresses(self):
        result = evaluate_parity(
            report(), report(run=99, blackboard=200, redis=5, pool=2)
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failures"], ["run_throughput_ops_per_second"])

    def test_incomplete_when_measurement_is_missing(self):
        legacy = report()
        del legacy["metrics"]["redis_queue_latency_p95_ms"]
        result = evaluate_parity(legacy, report())
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("legacy:metrics.redis_queue_latency_p95_ms", result["missing"])

    def test_incomplete_when_hardware_or_workload_does_not_match(self):
        platform = report()
        platform["environment"]["cpu_count"] = 8
        result = evaluate_parity(report(), platform)
        self.assertEqual(result["status"], "incomplete")
        self.assertIn("environment.cpu_count", result["mismatches"])

    def test_explicit_regression_tolerance_is_applied(self):
        result = evaluate_parity(
            report(), report(run=99.5, blackboard=199, redis=5.1, pool=2.02),
            max_regression_pct=2,
        )
        self.assertEqual(result["status"], "pass")

    def test_rejects_negative_or_over_100_tolerance(self):
        for tolerance in (-1, 100.01):
            with self.subTest(tolerance=tolerance):
                with self.assertRaisesRegex(ValueError, "between 0 and 100"):
                    evaluate_parity(report(), report(), max_regression_pct=tolerance)

    def test_rejects_non_finite_tolerance(self):
        for tolerance in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(tolerance=tolerance):
                with self.assertRaisesRegex(ValueError, "finite"):
                    evaluate_parity(report(), report(), max_regression_pct=tolerance)

    def test_non_finite_metrics_are_incomplete(self):
        for invalid in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(metric=invalid):
                legacy = report()
                legacy["metrics"]["run_throughput_ops_per_second"] = invalid
                result = evaluate_parity(legacy, report())
                self.assertEqual(result["status"], "incomplete")
                self.assertIn(
                    "legacy:metrics.run_throughput_ops_per_second",
                    result["missing"],
                )


if __name__ == "__main__":
    unittest.main()
