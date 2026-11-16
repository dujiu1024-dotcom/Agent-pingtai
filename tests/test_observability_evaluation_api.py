from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.observability import create_observability_router
from agent_platform.identity.models import Principal
from agent_platform.observability.evaluation import EvalCase, EvalDataset, EvalGatePolicy
from agent_platform.observability.evaluation_runner import (
    EvalOutput, EvaluationRunService,
)


class EvaluationRepository:
    def __init__(self):
        self.dataset = EvalDataset(
            "release-smoke", 2,
            (EvalCase("case-1", expected_terms=("safe",)),),
        )
        self.saved = []
        self.history_calls = []

    async def load_dataset(self, dataset_id, version):
        if (dataset_id, version) != ("release-smoke", 2):
            raise KeyError("dataset not found")
        return self.dataset

    async def save_report(
        self, report, *, model_key, gate_passed, gate_failures, tenant_id="",
    ):
        self.saved.append((tenant_id, model_key, gate_passed, gate_failures))
        return "eval-run-1"

    async def latest_reports(self, dataset_id, *, tenant_id, limit=20):
        self.history_calls.append((dataset_id, tenant_id, limit))
        return [{"eval_run_id": "eval-run-1", "tenant_id": tenant_id}]


class EvaluationRunner:
    async def run_case(self, case, *, tenant_id, model_key):
        return EvalOutput("safe result")


class ObservabilityEvaluationApiTests(unittest.TestCase):
    def setUp(self):
        self.repository = EvaluationRepository()
        self.service = EvaluationRunService(
            repository=self.repository, runner=EvaluationRunner(),
            policy=EvalGatePolicy(1.0, 1.0, 0.8),
        )
        self.app = FastAPI()
        self.app.include_router(create_observability_router(
            metrics=None,
            evaluation_repository=self.repository,
            evaluation_run_service=self.service,
            authorize_eval_manage=lambda principal: True,
        ))

        @self.app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("user-1", "tenant-a", "session-1", "user@example.com", "User"),
            )
            return await call_next(request)

        self.client = TestClient(self.app)

    def test_evaluation_run_persists_tenant_scoped_gate_evidence(self):
        response = self.client.post(
            "/api/platform/v1/observability/evaluations/run",
            json={
                "dataset_id": "release-smoke",
                "dataset_version": 2,
                "model_key": "provider/model@1",
            },
        )
        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()["gate_passed"])
        self.assertEqual(response.json()["eval_run_id"], "eval-run-1")
        self.assertEqual(
            self.repository.saved,
            [("tenant-a", "provider/model@1", True, ())],
        )

    def test_evaluation_history_is_filtered_by_authenticated_tenant(self):
        response = self.client.get(
            "/api/platform/v1/observability/evaluations?dataset_id=release-smoke",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["tenant_id"], "tenant-a")
        self.assertEqual(
            self.repository.history_calls,
            [("release-smoke", "tenant-a", 20)],
        )

    def test_evaluation_execution_requires_explicit_authorization(self):
        app = FastAPI()
        app.include_router(create_observability_router(
            metrics=None, evaluation_run_service=self.service,
            authorize_eval_manage=lambda principal: False,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("user-1", "tenant-a", "session-1", "user@example.com", "User"),
            )
            return await call_next(request)

        response = TestClient(app).post(
            "/api/platform/v1/observability/evaluations/run",
            json={
                "dataset_id": "release-smoke",
                "dataset_version": 2,
                "model_key": "provider/model@1",
            },
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
