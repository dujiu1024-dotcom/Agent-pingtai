from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.observability import create_observability_router
from agent_platform.identity.models import Principal


class ScoreRepository:
    async def save_human_score(self, **kwargs):
        self.saved = kwargs
        return "score-1"


class EvaluationGovernanceApiTests(unittest.TestCase):
    def _client(self, *, review_permission=None):
        repository = ScoreRepository()
        app = FastAPI()
        app.include_router(create_observability_router(
            metrics=None, evaluation_repository=repository,
            authorize_eval_manage=lambda principal: True,
            authorize_eval_review=review_permission,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("user-1", "tenant-a", "session-1", "reviewer@example.test", "Reviewer"),
            )
            return await call_next(request)

        return TestClient(app), repository

    def test_general_eval_manage_does_not_grant_human_review(self):
        client, _ = self._client()
        response = client.post(
            "/api/platform/v1/observability/evaluations/run-1/scores",
            json={"case_id": "case-1", "score": 0.8, "rationale": "checked"},
        )
        self.assertEqual(response.status_code, 403)

    def test_dedicated_reviewer_permission_allows_tenant_scoped_score(self):
        client, repository = self._client(review_permission=lambda principal: True)
        response = client.post(
            "/api/platform/v1/observability/evaluations/run-1/scores",
            json={"case_id": "case-1", "score": 0.8, "rationale": "checked"},
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(repository.saved["tenant_id"], "tenant-a")
        self.assertEqual(repository.saved["reviewer_id"], "user-1")


if __name__ == "__main__":
    unittest.main()
