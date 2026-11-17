from __future__ import annotations

import unittest
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.observability import create_observability_router
from agent_platform.identity.models import Principal
from agent_platform.observability.evaluation_comparison import EvaluationComparisonService


class Repository:
    def __init__(self):
        now = datetime.now(timezone.utc)
        self.rows = [
            {"tenant_id": "tenant-a", "dataset_id": "support", "dataset_version": 1,
             "eval_run_id": "run-a", "model_key": "model-a", "gate_passed": True,
             "created_at": now, "report": {"pass_rate": 0.9, "average_score": 0.9}},
            {"tenant_id": "tenant-a", "dataset_id": "support", "dataset_version": 1,
             "eval_run_id": "run-b", "model_key": "model-b", "gate_passed": True,
             "created_at": now, "report": {"pass_rate": 0.95, "average_score": 0.95}},
        ]
        self.saved = {}

    async def list_variant_runs(self, *, tenant_id, dataset_id, dataset_version, limit):
        return [row for row in self.rows if row["tenant_id"] == tenant_id
                and row["dataset_id"] == dataset_id
                and row["dataset_version"] == dataset_version][:limit]

    async def save_variant_report(self, *, tenant_id, report):
        self.saved[report["report_id"]] = dict(report)
        return report["report_id"]

    async def load_variant_report(self, *, tenant_id, report_id):
        item = self.saved.get(report_id)
        if item is None or item["tenant_id"] != tenant_id:
            raise KeyError("not found")
        return item


class EvaluationComparisonApiTests(unittest.TestCase):
    def setUp(self):
        self.service = EvaluationComparisonService(repository=Repository())
        app = FastAPI()
        app.include_router(create_observability_router(
            metrics=None, evaluation_comparison_service=self.service,
            authorize_eval_manage=lambda actor: actor.user_id == "reviewer",
        ))

        @app.middleware("http")
        async def principal(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("reviewer", "tenant-a", "session", "reviewer@example.test", "Reviewer"),
            )
            return await call_next(request)

        self.client = TestClient(app)

    def test_create_read_and_html_export(self):
        created = self.client.post("/api/platform/v1/observability/evaluations/comparisons", json={
            "dataset_id": "support", "dataset_version": 1,
        })
        self.assertEqual(created.status_code, 201)
        report_id = created.json()["report_id"]
        detail = self.client.get(
            f"/api/platform/v1/observability/evaluations/comparisons/{report_id}",
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["recommended"]["model_key"], "model-b")
        exported = self.client.get(
            f"/api/platform/v1/observability/evaluations/comparisons/{report_id}/export",
            params={"format": "html"},
        )
        self.assertEqual(exported.status_code, 200)
        self.assertIn("text/html", exported.headers["content-type"])
        self.assertIn("Recommended", exported.text)

    def test_invalid_export_format_is_rejected(self):
        created = self.client.post("/api/platform/v1/observability/evaluations/comparisons", json={
            "dataset_id": "support", "dataset_version": 1,
        })
        report_id = created.json()["report_id"]
        response = self.client.get(
            f"/api/platform/v1/observability/evaluations/comparisons/{report_id}/export",
            params={"format": "csv"},
        )
        self.assertEqual(response.status_code, 422)


if __name__ == "__main__":
    unittest.main()
