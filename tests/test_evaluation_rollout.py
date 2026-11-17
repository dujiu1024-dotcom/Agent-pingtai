from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.evaluation_rollout import create_evaluation_rollout_router
from agent_platform.identity.models import Principal
from agent_platform.observability.evaluation_rollout import EvaluationRolloutBridge


class ReportStore:
    def __init__(self, report):
        self.report = report

    async def get(self, tenant_id, experiment_id):
        if tenant_id != self.report.tenant_id:
            return None
        return self.report


class Rollout:
    def __init__(self):
        self.calls = []

    async def configure(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(weights=tuple(sorted(kwargs["weights"].items())), revision=3)


def report(errors=()):
    metrics = (
        SimpleNamespace(variant="stable", case_count=2, average_score=.8, pass_rate=1, average_latency_ms=20),
        SimpleNamespace(variant="candidate", case_count=2, average_score=.92, pass_rate=1, average_latency_ms=25),
        SimpleNamespace(variant="fast", case_count=2, average_score=.86, pass_rate=1, average_latency_ms=10),
    )
    matrix = tuple(
        SimpleNamespace(case_id=case, error=error)
        for case, error in (("one", errors[0] if errors else None), ("two", None))
    )
    return SimpleNamespace(
        tenant_id="tenant-a", prompt_id="assistant", baseline_variant="stable",
        metrics=metrics, matrix=matrix,
    )


class EvaluationRolloutBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = ReportStore(report())
        self.rollout = Rollout()
        self.bridge = EvaluationRolloutBridge(
            experiment_repository=self.store, rollout_service=self.rollout,
        )

    async def test_automatic_promotion_selects_best_candidate_and_preserves_canary(self):
        result = await self.bridge.apply(
            tenant_id="tenant-a", experiment_id="eval-1", actor="operator",
            mode="automatic", rollout_percentage=15, minimum_score_gain=.05,
        )
        self.assertEqual(result.selected_variant, "candidate")
        self.assertEqual(dict(result.weights), {"candidate": 15.0, "stable": 85.0})
        self.assertEqual(self.rollout.calls[0]["actor"], "operator")

    async def test_manual_trigger_can_cut_over_selected_variant(self):
        result = await self.bridge.apply(
            tenant_id="tenant-a", experiment_id="eval-1", actor="operator",
            mode="manual", variant="fast", rollout_percentage=100,
        )
        self.assertEqual(result.selected_variant, "fast")
        self.assertEqual(dict(result.weights), {"fast": 100.0})

    async def test_score_threshold_and_failed_case_fail_closed(self):
        with self.assertRaises(ValueError):
            await self.bridge.apply(
                tenant_id="tenant-a", experiment_id="eval-1", actor="operator",
                mode="automatic", minimum_score_gain=.2,
            )
        self.store.report = report(errors=("TimeoutError",))
        with self.assertRaises(ValueError):
            await self.bridge.apply(
                tenant_id="tenant-a", experiment_id="eval-1", actor="operator",
                mode="automatic",
            )
        self.assertEqual(self.rollout.calls, [])

    async def test_tenant_and_case_coverage_are_enforced(self):
        with self.assertRaises(KeyError):
            await self.bridge.apply(
                tenant_id="tenant-b", experiment_id="eval-1", actor="operator",
                mode="manual", variant="candidate",
            )
        self.store.report.metrics = (
            SimpleNamespace(variant="stable", case_count=2, average_score=.8, pass_rate=1, average_latency_ms=20),
            SimpleNamespace(variant="candidate", case_count=1, average_score=.9, pass_rate=1, average_latency_ms=10),
        )
        with self.assertRaises(ValueError):
            await self.bridge.apply(
                tenant_id="tenant-a", experiment_id="eval-1", actor="operator",
                mode="manual", variant="candidate",
            )
        self.assertEqual(self.rollout.calls, [])


class Audit:
    def __init__(self):
        self.events = []
        self.fail = False

    async def record(self, **event):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


class EvaluationRolloutApiTests(unittest.TestCase):
    def setUp(self):
        self.store = ReportStore(report())
        self.rollout = Rollout()
        self.bridge = EvaluationRolloutBridge(
            experiment_repository=self.store, rollout_service=self.rollout,
        )
        self.audit = Audit()
        self.app = FastAPI()
        self.app.include_router(create_evaluation_rollout_router(
            bridge=self.bridge, authorize=lambda actor, prompt_id, action: True,
            audit_writer=self.audit,
        ))

        @self.app.middleware("http")
        async def identity(request, call_next):
            request.state.__setattr__(
                "identity.principal",
                Principal("operator", "tenant-a", "session-1", "operator@example.com", "Operator"),
            )
            return await call_next(request)

        self.client = TestClient(self.app)

    def test_manual_rollout_requires_audit_and_returns_policy(self):
        response = self.client.post(
            "/api/platform/v1/evaluations/eval-1/rollout",
            json={"mode": "manual", "variant": "candidate", "rollout_percentage": 20},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["weights"], {"candidate": 20.0, "stable": 80.0})
        self.assertEqual(self.audit.events[0]["action"], "evaluation.prompt_rollout.requested")

    def test_audit_failure_prevents_rollout_mutation(self):
        self.audit.fail = True
        response = self.client.post(
            "/api/platform/v1/evaluations/eval-1/rollout",
            json={"mode": "manual", "variant": "candidate"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.rollout.calls, [])


if __name__ == "__main__":
    unittest.main()
