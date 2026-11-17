from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from agent_platform.api.evaluation_rollout import create_evaluation_rollout_router
from agent_platform.identity.models import Principal


class PrincipalMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        request.state.identity = SimpleNamespace(
            principal=Principal("user-1", "tenant-a", "session-1", "u@example.test", "User"),
        )
        return await call_next(request)


class Bridge:
    def __init__(self):
        self.calls = []

    async def promote(self, **kwargs):
        self.calls.append(kwargs)
        return {"prompt_id": kwargs["prompt_id"], "candidate_percentage": kwargs["candidate_percentage"]}


class EvaluationRolloutApiTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge()
        self.app = FastAPI()
        self.app.add_middleware(PrincipalMiddleware)
        self.app.include_router(create_evaluation_rollout_router(
            bridge=self.bridge,
            authorize_eval_manage=lambda actor: actor.tenant_id == "tenant-a",
            authorize_prompt=lambda actor, prompt_id, action: (
                actor.tenant_id == "tenant-a" and action == "publish"
            ),
        ))
        self.client = TestClient(self.app)
        self.url = "/api/platform/v1/observability/evaluations/comparisons/report-1/promote"
        self.body = {
            "prompt_id": "support",
            "model_to_prompt_variant": {"model-new": "candidate", "model-old": "stable"},
            "baseline_model_key": "model-old",
            "candidate_percentage": 20,
        }

    def test_explicit_promotion_requires_both_evaluation_and_prompt_publish_permissions(self):
        response = self.client.post(self.url, json=self.body)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.bridge.calls[0]["tenant_id"], "tenant-a")
        self.assertEqual(self.bridge.calls[0]["actor_id"], "user-1")

    def test_evaluation_or_prompt_permission_denial_blocks_promotion(self):
        app = FastAPI()
        app.add_middleware(PrincipalMiddleware)
        app.include_router(create_evaluation_rollout_router(
            bridge=self.bridge,
            authorize_eval_manage=lambda actor: False,
            authorize_prompt=lambda actor, prompt_id, action: True,
        ))
        response = TestClient(app).post(self.url, json=self.body)
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.bridge.calls, [])

    def test_invalid_request_does_not_reach_bridge(self):
        response = self.client.post(self.url, json={"prompt_id": "support"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.bridge.calls, [])


if __name__ == "__main__":
    unittest.main()
