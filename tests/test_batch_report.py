from __future__ import annotations

import csv
import json
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agent_platform.batch import BatchExecutionResult, BatchTaskResult, build_batch_report


class BatchReportTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.tasks = (
            BatchTaskResult(
                "batch:0", 0, "succeeded",
                {"passed": True, "score": 0.9, "cost": Decimal("0.12"), "latency_ms": 35},
                None, None, self.now, self.now + timedelta(milliseconds=35), 35,
            ),
            BatchTaskResult(
                "batch:1", 1, "succeeded",
                {"passed": False, "score": 0.4, "cost": 0.08},
                None, None, self.now, self.now + timedelta(milliseconds=20), 20,
            ),
            BatchTaskResult(
                "batch:2", 2, "failed", None, "TimeoutError", "timed out",
                self.now, self.now + timedelta(milliseconds=50), 50,
            ),
        )
        self.result = BatchExecutionResult(
            "batch", "tenant-a", "template-v1", 3, 2, 1,
            self.now, self.now + timedelta(milliseconds=60), 60, self.tasks,
        )

    def test_aggregates_pass_rate_score_cost_latency_and_details(self):
        report = build_batch_report(self.result)
        self.assertEqual((report.total, report.succeeded, report.failed, report.passed), (3, 2, 1, 1))
        self.assertAlmostEqual(report.pass_rate, 1 / 3)
        self.assertAlmostEqual(report.average_score, 0.65)
        self.assertEqual(report.total_cost, Decimal("0.20"))
        self.assertAlmostEqual(report.average_latency_ms, 35)
        self.assertEqual([task.task_id for task in report.tasks], ["batch:0", "batch:1", "batch:2"])
        self.assertEqual(report.tasks[2].error_type, "TimeoutError")
        self.assertEqual(report.tasks[2].latency_ms, 50)

    def test_json_export_contains_summary_and_per_task_diagnostics(self):
        payload = json.loads(build_batch_report(self.result).to_json())
        self.assertEqual(payload["summary"]["total"], 3)
        self.assertEqual(payload["summary"]["total_cost"], "0.20")
        self.assertEqual(payload["tasks"][2]["error_message"], "timed out")
        self.assertEqual(payload["tasks"][0]["output"]["score"], 0.9)

    def test_csv_export_has_one_ordered_row_per_task_and_escaped_output(self):
        report = build_batch_report(self.result)
        rows = list(csv.DictReader(report.to_csv().splitlines()))
        self.assertEqual(len(rows), 3)
        self.assertEqual([row["task_id"] for row in rows], ["batch:0", "batch:1", "batch:2"])
        self.assertEqual(rows[0]["cost"], "0.12")
        self.assertEqual(rows[2]["error_type"], "TimeoutError")

    def test_missing_metrics_use_documented_defaults(self):
        tasks = (BatchTaskResult(
            "batch:0", 0, "succeeded", "plain output", None, None,
            self.now, self.now, 12,
        ),)
        result = BatchExecutionResult(
            "batch", "tenant-a", "template", 1, 1, 0,
            self.now, self.now, 12, tasks,
        )
        report = build_batch_report(result)
        self.assertEqual(report.pass_rate, 1)
        self.assertIsNone(report.average_score)
        self.assertEqual(report.total_cost, Decimal("0"))
        self.assertEqual(report.average_latency_ms, 12)

    def test_invalid_metrics_and_inconsistent_totals_are_rejected(self):
        invalid = (BatchTaskResult(
            "batch:0", 0, "succeeded", {"cost": -1}, None, None,
            self.now, self.now, 1,
        ),)
        result = BatchExecutionResult(
            "batch", "tenant-a", "template", 1, 1, 0,
            self.now, self.now, 1, invalid,
        )
        with self.assertRaisesRegex(ValueError, "cost"):
            build_batch_report(result)
        invalid_score = (BatchTaskResult(
            "batch:0", 0, "succeeded", {"score": 1.1}, None, None,
            self.now, self.now, 1,
        ),)
        bad_score_result = BatchExecutionResult(
            "batch", "tenant-a", "template", 1, 1, 0,
            self.now, self.now, 1, invalid_score,
        )
        with self.assertRaisesRegex(ValueError, "score"):
            build_batch_report(bad_score_result)
        inconsistent = BatchExecutionResult(
            "batch", "tenant-a", "template", 2, 1, 0,
            self.now, self.now, 1, invalid,
        )
        with self.assertRaisesRegex(ValueError, "totals"):
            build_batch_report(inconsistent)


if __name__ == "__main__":
    unittest.main()
