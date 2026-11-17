from __future__ import annotations

import unittest
from types import SimpleNamespace

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


if __name__ == "__main__":
    unittest.main()
