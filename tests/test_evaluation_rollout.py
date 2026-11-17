from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.observability.evaluation_rollout import EvaluationPromptRolloutBridge


def report(tenant="tenant-a", winner_gate=True, winner_score=0.92):
    return {
        "tenant_id": tenant,
        "recommended": {"model_key": "model-new", "eval_run_id": "run-new"},
        "variants": [
            {"model_key": "model-new", "eval_run_id": "run-new", "gate_passed": winner_gate,
             "metrics": {"average_score": winner_score, "pass_rate": 0.95}},
            {"model_key": "model-old", "eval_run_id": "run-old", "gate_passed": True,
             "metrics": {"average_score": 0.85, "pass_rate": 0.9}},
        ],
    }


class ComparisonStore:
    def __init__(self, value):
        self.value = value
        self.calls = []

    async def get_report(self, *, tenant_id, report_id):
        self.calls.append((tenant_id, report_id))
        return self.value


class Rollout:
    def __init__(self):
        self.calls = []

    async def configure(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            weights=tuple(sorted(kwargs["weights"].items())),
            revision=4,
        )


class EvaluationPromptRolloutBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = ComparisonStore(report())
        self.rollout = Rollout()
        self.bridge = EvaluationPromptRolloutBridge(
            comparison_service=self.store, rollout_service=self.rollout,
        )
        self.mapping = {"model-new": "candidate", "model-old": "stable"}

    async def test_promotes_recommendation_to_percentage_canary_and_audits_actor(self):
        result = await self.bridge.promote(
            tenant_id="tenant-a", report_id="report-1", prompt_id="support",
            model_to_prompt_variant=self.mapping, baseline_model_key="model-old",
            candidate_percentage=15, actor_id="operator", minimum_score_gain=0.05,
        )
        self.assertEqual(result["recommended_prompt_variant"], "candidate")
        self.assertEqual(result["weights"], {"candidate": 15.0, "stable": 85.0})
        self.assertEqual(result["quality_gain"], 0.07)
        self.assertEqual(result["policy_revision"], 4)
        self.assertEqual(self.rollout.calls[0]["actor"], "operator")
        self.assertEqual(self.store.calls, [("tenant-a", "report-1")])

    async def test_full_cutover_uses_only_winner_variant(self):
        result = await self.bridge.promote(
            tenant_id="tenant-a", report_id="report-1", prompt_id="support",
            model_to_prompt_variant=self.mapping, baseline_model_key="model-old",
            candidate_percentage=100, actor_id="operator",
        )
        self.assertEqual(result["weights"], {"candidate": 100.0})

    async def test_tenant_mismatch_gate_failure_and_insufficient_gain_fail_closed(self):
        self.store.value = report(tenant="tenant-b")
        with self.assertRaises(PermissionError):
            await self.bridge.promote(
                tenant_id="tenant-a", report_id="report-1", prompt_id="support",
                model_to_prompt_variant=self.mapping, baseline_model_key="model-old",
                candidate_percentage=10, actor_id="operator",
            )
        self.store.value = report(winner_gate=False)
        with self.assertRaisesRegex(ValueError, "evaluation gate"):
            await self.bridge.promote(
                tenant_id="tenant-a", report_id="report-1", prompt_id="support",
                model_to_prompt_variant=self.mapping, baseline_model_key="model-old",
                candidate_percentage=10, actor_id="operator",
            )
        self.store.value = report(winner_score=0.86)
        with self.assertRaisesRegex(ValueError, "minimum quality gain"):
            await self.bridge.promote(
                tenant_id="tenant-a", report_id="report-1", prompt_id="support",
                model_to_prompt_variant=self.mapping, baseline_model_key="model-old",
                candidate_percentage=10, actor_id="operator", minimum_score_gain=0.05,
            )
        self.assertEqual(self.rollout.calls, [])

    async def test_missing_mapping_duplicate_targets_and_invalid_percentage_rejected(self):
        for mapping, percentage in (
            ({"model-new": "candidate"}, 10),
            ({"model-new": "candidate", "model-old": "candidate"}, 10),
            (self.mapping, 0),
        ):
            with self.assertRaises(ValueError):
                await self.bridge.promote(
                    tenant_id="tenant-a", report_id="report-1", prompt_id="support",
                    model_to_prompt_variant=mapping, baseline_model_key="model-old",
                    candidate_percentage=percentage, actor_id="operator",
                )
        self.assertEqual(self.rollout.calls, [])


if __name__ == "__main__":
    unittest.main()
