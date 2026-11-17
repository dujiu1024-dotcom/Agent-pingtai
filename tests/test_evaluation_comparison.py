from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.observability.evaluation_comparison import EvaluationComparisonService


class FakeRepository:
    def __init__(self):
        self.rows = []
        self.saved = {}

    async def list_variant_runs(self, *, tenant_id, dataset_id, dataset_version, limit):
        return [row for row in self.rows if row["tenant_id"] == tenant_id
                and row["dataset_id"] == dataset_id
                and row["dataset_version"] == dataset_version][:limit]

    async def save_variant_report(self, *, tenant_id, report):
        self.saved[report["report_id"]] = dict(report)
        return report["report_id"]

    async def load_variant_report(self, *, tenant_id, report_id):
        report = self.saved.get(report_id)
        if report is None or report["tenant_id"] != tenant_id:
            raise KeyError("not found")
        return report


class Pdf:
    def render(self, report):
        return b"%PDF-1.4\n"


class EvaluationComparisonTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = FakeRepository()
        now = datetime(2026, 11, 17, tzinfo=timezone.utc)
        self.repo.rows = [
            {"tenant_id": "tenant-a", "dataset_id": "support", "dataset_version": 2,
             "eval_run_id": "run-a", "model_key": "model-a", "gate_passed": False,
             "created_at": now, "report": {"pass_rate": 0.8, "average_score": 0.82,
             "metrics": {"cost_usd": 0.02, "latency_ms": 800}}},
            {"tenant_id": "tenant-a", "dataset_id": "support", "dataset_version": 2,
             "eval_run_id": "run-b", "model_key": "model-b", "gate_passed": True,
             "created_at": now, "report": {"pass_rate": 0.95, "average_score": 0.93,
             "average_recall_at_k": 0.9,
             "metrics": {"cost_usd": 0.01, "latency_ms": 500, "tokens": 1200}}},
            {"tenant_id": "tenant-b", "dataset_id": "support", "dataset_version": 2,
             "eval_run_id": "foreign", "model_key": "foreign", "gate_passed": True,
             "created_at": now, "report": {"pass_rate": 1, "average_score": 1}},
            {"tenant_id": "tenant-a", "dataset_id": "support", "dataset_version": 1,
             "eval_run_id": "old", "model_key": "old", "gate_passed": True,
             "created_at": now, "report": {"pass_rate": 1, "average_score": 1}},
        ]
        self.service = EvaluationComparisonService(repository=self.repo, pdf_renderer=Pdf())

    async def test_matrix_recommends_gate_passing_quality_variant_and_persists(self):
        report = await self.service.create_report(
            tenant_id="tenant-a", dataset_id="support", dataset_version=2, created_by="reviewer",
        )
        self.assertEqual(report["variant_count"], 2)
        self.assertEqual(report["recommended"]["eval_run_id"], "run-b")
        self.assertEqual(report["metric_names"], [
            "average_recall_at_k", "average_score", "cost_usd", "latency_ms", "pass_rate", "tokens",
        ])
        self.assertAlmostEqual(
            report["comparisons_to_recommended"][0]["delta_to_recommended"]["cost_usd"], 0.01,
        )
        self.assertEqual(await self.service.get_report(
            tenant_id="tenant-a", report_id=report["report_id"],
        ), report)

    async def test_run_ids_are_validated_against_tenant_and_dataset(self):
        with self.assertRaises(KeyError):
            await self.service.create_report(
                tenant_id="tenant-a", dataset_id="support", dataset_version=2,
                eval_run_ids=("run-b", "foreign"), created_by="reviewer",
            )
        with self.assertRaises(ValueError):
            await self.service.create_report(
                tenant_id="tenant-a", dataset_id="support", dataset_version=2,
                eval_run_ids=("run-b", "run-b"), created_by="reviewer",
            )

    async def test_missing_or_single_variant_fails_closed(self):
        with self.assertRaises(ValueError):
            await self.service.create_report(
                tenant_id="tenant-a", dataset_id="missing", dataset_version=1, created_by="reviewer",
            )

    async def test_html_escapes_untrusted_values_and_pdf_signature(self):
        report = await self.service.create_report(
            tenant_id="tenant-a", dataset_id="<script>x</script>", dataset_version=2,
            created_by="reviewer",
        )
        rendered = self.service.render_html(report)
        self.assertNotIn("<script>x</script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertTrue(self.service.render_pdf(report).startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
