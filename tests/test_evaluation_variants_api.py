from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from agent_platform.api.evaluation_variants import create_eval_variants_router
from agent_platform.identity.models import Principal


class FakeService:
    async def run(self, **kwargs):
        from types import SimpleNamespace
        from agent_platform.observability.evaluation_variants import (
            VariantAggregate, VariantCaseScore,
        )
        self.kwargs = kwargs
        return SimpleNamespace(
            eval_run_id="run-1", dataset_id="support", dataset_version=1,
            variant_results=(VariantAggregate(
                "model-a", 1, 1, 1.0, 1.0, 2, 3, 5, "0.001",
                12.0, 12.0, 12.0,
                (VariantCaseScore("case-1", 1.0, True, "rule"),),
            ),),
        )


class PrincipalMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.identity = type("Identity", (), {
            "principal": Principal("user-1", "tenant-1", "session-1", "u@example.test", "User"),
        })()
        return await call_next(request)


class EvaluationVariantsApiTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeService()
        self.app = FastAPI()
        self.app.add_middleware(PrincipalMiddleware)
        self.app.include_router(create_eval_variants_router(
            service=self.service, authorize_manage=lambda actor: actor.tenant_id == "tenant-1",
        ))
        self.client = TestClient(self.app)

    def test_authenticated_tenant_runs_variants_and_returns_usage_metrics(self):
        response = self.client.post("/api/platform/v1/observability/evaluations/variants/run", json={
            "dataset_id": "support", "dataset_version": 1,
            "prompt": "answer {{input}}", "model_variants": ["model-a"],
        })
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["variants"][0]["total_tokens"], 5)
        self.assertEqual(body["variants"][0]["cost_usd"], "0.001")
        self.assertEqual(self.service.kwargs["tenant_id"], "tenant-1")

    def test_permission_denied(self):
        app = FastAPI()
        app.add_middleware(PrincipalMiddleware)
        app.include_router(create_eval_variants_router(
            service=self.service, authorize_manage=lambda actor: False,
        ))
        response = TestClient(app).post(
            "/api/platform/v1/observability/evaluations/variants/run", json={},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
