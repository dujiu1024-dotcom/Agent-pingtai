from __future__ import annotations

import unittest

from scripts.verify_switch_l4_t07_acceptance import build_report


def _benchmark():
    metric = {
        "operations": 40,
        "ops_per_second": 100.0,
        "latency_ms": {"p50": 1.0, "p95": 2.0, "p99": 3.0, "max": 4.0},
    }
    return {
        "configuration": {"workers": 4, "operations_per_worker": 10},
        "legacy_sqlite": {
            "run_lifecycle_transaction": dict(metric),
            "blackboard_write_read": dict(metric),
        },
        "platform_postgres_pool": {
            "status": "measured",
            "metrics": {
                "run_lifecycle_transaction": dict(metric),
                "blackboard_write_read": dict(metric),
            },
        },
        "platform_redis_queue": {
            "status": "measured",
            "metrics": {
                "messages": 40,
                "ops_per_second": 90.0,
                "latency_ms": {"p50": 1.2, "p95": 2.2, "p99": 3.2, "max": 4.2},
            },
        },
    }


class SwitchL4T07AcceptanceReportTests(unittest.TestCase):
    def test_builds_measured_report_without_claiming_production_parity(self):
        report = build_report(_benchmark())
        self.assertEqual(report["automated_ci_status"], "passed")
        self.assertEqual(report["performance"]["status"], "measured")
        self.assertEqual(report["performance"]["parity_conclusion"], "incomplete")
        self.assertEqual(report["failover"]["status"], "automated_adapter_drills_passed")
        self.assertEqual(report["failover"]["production_failover"], "not_tested")

    def test_rejects_missing_postgres_measurement(self):
        benchmark = _benchmark()
        benchmark["platform_postgres_pool"]["status"] = "not_run"
        with self.assertRaisesRegex(ValueError, "PostgreSQL benchmark"):
            build_report(benchmark)

    def test_rejects_missing_redis_measurement(self):
        benchmark = _benchmark()
        benchmark["platform_redis_queue"]["status"] = "not_run"
        with self.assertRaisesRegex(ValueError, "Redis benchmark"):
            build_report(benchmark)

    def test_rejects_incomplete_operation_count(self):
        benchmark = _benchmark()
        benchmark["platform_postgres_pool"]["metrics"]["run_lifecycle_transaction"]["operations"] = 39
        with self.assertRaisesRegex(ValueError, "expected 40"):
            build_report(benchmark)

    def test_rejects_missing_latency_percentiles(self):
        benchmark = _benchmark()
        del benchmark["platform_redis_queue"]["metrics"]["latency_ms"]["p99"]
        with self.assertRaisesRegex(ValueError, "Redis queue latency"):
            build_report(benchmark)


if __name__ == "__main__":
    unittest.main()
