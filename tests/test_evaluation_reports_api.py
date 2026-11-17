from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from agent_platform.api.evaluation_variants import create_eval_variants_router
from agent_platform.identity.models import Principal


class PrincipalMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.identity = SimpleNamespace(
            principal=Principal("user-1", "tenant-a", "session-1", "u@example.test", "User"),
        )
        return await call_next(request)


class RunRepository:
    async def get_run(self, *, tenant_id, run_id):
        if tenant_id != "tenant-a" or run_id != "eval-1":
            raise KeyError("not found")
        return {
            "eval_run_id": "eval-1", "dataset_id": "support", "dataset_version": 1,
            "variants": [
                {"model_variant": "m1", "case_count": 1, "passed_count": 1,
                 "pass_rate": 1.0, "average_score": 1.0, "input_tokens": 3,
                 "output_tokens": 2, "total_tokens": 5, "cost_usd": "0.001",
                 "average_latency_ms": 10.0, "p50_latency_ms": 10.0, "p95_latency_ms": 10.0},
            ],
        }


class EvaluationReportApiTests(unittest.TestCase):
    def setUp(self):
        self.app = FastAPI()
        self.app.add_middleware(PrincipalMiddleware)
        self.app.include_router(create_eval_variants_router(
            service=SimpleNamespace(run_repository=RunRepository()),
            authorize_manage=lambda actor: actor.tenant_id == "tenant-a",
        ))
        self.client = TestClient(self.app)

    def test_comparison_and_html_pdf_exports_are_tenant_scoped(self):
        base = "/api/platform/v1/observability/evaluations/variants/eval-1"
        comparison = self.client.get(base + "/comparison")
        self.assertEqual(comparison.status_code, 200)
        self.assertEqual(comparison.json()["recommendation"]["model_variant"], "m1")
        html = self.client.get(base + "/report?format=html")
        self.assertEqual(html.status_code, 200)
        self.assertIn("text/html", html.headers["content-type"])
        pdf = self.client.get(base + "/report?format=pdf")
        self.assertEqual(pdf.status_code, 200)
        self.assertTrue(pdf.content.startswith(b"%PDF-1.4"))
        missing = self.client.get(
            "/api/platform/v1/observability/evaluations/variants/other/comparison",
        )
        self.assertEqual(missing.status_code, 404)

    def test_unknown_export_format_is_rejected(self):
        response = self.client.get(
            "/api/platform/v1/observability/evaluations/variants/eval-1/report?format=docx",
        )
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
