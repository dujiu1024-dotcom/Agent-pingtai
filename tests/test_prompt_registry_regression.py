from __future__ import annotations

import math
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.prompts import create_prompt_registry_router
from agent_platform.identity.models import Principal
from agent_platform.observability.prompts import PromptRegistry, tenant_prompt_key


class PromptRegistryRegressionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = PromptRegistry()
        self.prompt_key = tenant_prompt_key("tenant-a", "assistant")
        self.stable = self.registry.register(self.prompt_key, "Stable: {question}")
        self.candidate = self.registry.register(self.prompt_key, "Candidate: {question}")
        self.registry.activate(self.prompt_key, self.stable.version, updated_by="release-manager")

    def test_canary_rejects_boolean_version_and_non_finite_or_out_of_range_weights(self) -> None:
        invalid_percentages = (True, False, 0, -1, 100.01, float("nan"), math.inf, -math.inf)
        for percentage in invalid_percentages:
            with self.subTest(percentage=percentage):
                with self.assertRaises(ValueError):
                    self.registry.set_canary(
                        self.prompt_key, self.candidate.version, percentage,
                        updated_by="release-manager",
                    )
                self.assertNotIn(self.prompt_key, self.registry._canary)

        with self.assertRaisesRegex(ValueError, "positive integer"):
            self.registry.set_canary(
                self.prompt_key, True, 10, updated_by="release-manager",
            )
        with self.assertRaisesRegex(ValueError, "updated_by"):
            self.registry.set_canary(self.prompt_key, self.candidate.version, 10, updated_by=" ")

    def test_version_activation_and_canary_resolution_are_deterministic(self) -> None:
        self.registry.set_canary(
            self.prompt_key, self.candidate.version, 100,
            updated_by="release-manager", evaluation_run_id="eval-approved",
        )
        self.assertEqual(
            self.registry.resolve(self.prompt_key, rollout_key="user-1").version,
            self.candidate.version,
        )
        self.assertEqual(
            self.registry.resolve(self.prompt_key, rollout_key="user-1").version,
            self.candidate.version,
        )
        self.registry.rollback(self.prompt_key, self.stable.version, updated_by="operator")
        self.assertNotIn(self.prompt_key, self.registry._canary)
        self.assertEqual(self.registry.resolve(self.prompt_key).version, self.stable.version)

    def _client(self, gate):
        app = FastAPI()
        app.include_router(create_prompt_registry_router(
            registry=self.registry,
            authorize=lambda actor, prompt_id, action: True,
            evaluate_gate=gate,
        ))

        @app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("user-1", "tenant-a", "session-1", "user@example.test", "User"),
            )
            return await call_next(request)

        return TestClient(app)

    def test_canary_gate_rejection_and_unavailable_gate_do_not_mutate_deployment(self) -> None:
        rejected = self._client(lambda **kwargs: {
            "gate_passed": False,
            "gate_failures": ["pass_rate_below_threshold"],
        }).post(
            "/api/platform/v1/prompts/assistant/canary",
            json={"version": self.candidate.version, "percentage": 10},
        )
        self.assertEqual(rejected.status_code, 409)
        self.assertNotIn(self.prompt_key, self.registry._canary)

        unavailable = self._client(None).post(
            "/api/platform/v1/prompts/assistant/canary",
            json={"version": self.candidate.version, "percentage": 10},
        )
        self.assertEqual(unavailable.status_code, 503)
        self.assertNotIn(self.prompt_key, self.registry._canary)

    def test_canary_requires_gate_evidence_and_records_evaluation_reference(self) -> None:
        calls = []
        client = self._client(lambda **kwargs: calls.append(kwargs) or {
            "gate_passed": True, "eval_run_id": "eval-approved",
        })
        response = client.post(
            "/api/platform/v1/prompts/assistant/canary",
            json={"version": self.candidate.version, "percentage": 10},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["eval_run_id"], "eval-approved")
        self.assertEqual(self.registry._canary[self.prompt_key], (self.candidate.version, 10.0))
        self.assertEqual(calls[0]["action"], "canary")
        self.assertEqual(calls[0]["tenant_id"], "tenant-a")


if __name__ == "__main__":
    unittest.main()
