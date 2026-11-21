from __future__ import annotations

import unittest

from agent_platform.observability.evaluation_governance import (
    EvaluationThresholdPolicy,
    compare_metric_reports,
    evaluate_thresholds,
    mask_reviewer_identity,
    redact_human_score_rows,
)


class EvaluationGovernanceTests(unittest.TestCase):
    def test_comparison_detects_metric_direction_regressions(self):
        result = compare_metric_reports(
            "baseline", "candidate",
            {"answer_term_f1": 0.92, "latency_ms": 100, "citation_recall": 0.9},
            {"answer_term_f1": 0.88, "latency_ms": 120, "citation_recall": 0.95},
        )
        self.assertEqual(set(result.regressions), {"answer_term_f1", "latency_ms"})
        deltas = {item.metric: item.delta for item in result.deltas}
        self.assertAlmostEqual(deltas["citation_recall"], 0.05)

    def test_threshold_policy_fails_closed_for_missing_metrics(self):
        policy = EvaluationThresholdPolicy(
            "production", 3, {"answer_term_f1": 0.9, "citation_recall": 0.8},
            {"forbidden_term_rate": 0.01},
        )
        passed, failures = evaluate_thresholds(
            {"answer_term_f1": 0.95, "forbidden_term_rate": 0.0}, policy,
        )
        self.assertFalse(passed)
        self.assertTrue(any(item.startswith("citation_recall:") for item in failures))

    def test_policy_rejects_ambiguous_threshold_configuration(self):
        with self.assertRaises(ValueError):
            EvaluationThresholdPolicy("bad", 1, {"score": 0.5}, {"score": 0.8})

    def test_reviewer_identity_is_masked_in_api_rows(self):
        rows = redact_human_score_rows([
            {"reviewer_id": "alice@example.test", "score": 0.8, "rationale": "good"},
        ])
        self.assertNotIn("reviewer_id", rows[0])
        self.assertEqual(rows[0]["reviewer_alias"], mask_reviewer_identity("alice@example.test"))
        self.assertNotIn("alice@example.test", str(rows))


    def test_gate_decision_hash_is_deterministic_for_payload(self):
        import asyncio

        from agent_platform.observability.evaluation_governance import (
            EvaluationGovernanceService,
        )

        class Repository:
            def __init__(self):
                self.evidence = []
            async def load_report_for_tenant(self, *, tenant_id, eval_run_id):
                if tenant_id != "tenant-a" or eval_run_id not in {"base", "candidate"}:
                    raise KeyError("not found")
                return {"metrics": {"answer_term_f1": 0.95, "citation_recall": 0.9}}
            async def save_comparison(self, *, tenant_id, comparison):
                self.comparison = comparison
            async def save_gate_evidence(self, evidence):
                self.evidence.append(evidence)

        async def run():
            repository = Repository()
            service = EvaluationGovernanceService(repository)
            policy = EvaluationThresholdPolicy("prod", 4, {"answer_term_f1": 0.9}, {})
            evidence = await service.decide_release(
                tenant_id="tenant-a", candidate_run_id="candidate",
                baseline_run_id="base", policy=policy,
                evidence_refs=("ci://run/123", "eval://candidate"),
            )
            self.assertTrue(evidence.passed)
            self.assertEqual(evidence.policy_version, 4)
            self.assertEqual(len(evidence.content_sha256), 64)
            self.assertEqual(repository.evidence, [evidence])
            self.assertIsNotNone(evidence.comparison_id)
        asyncio.run(run())

    def test_missing_evidence_reference_blocks_gate(self):
        import asyncio

        from agent_platform.observability.evaluation_governance import EvaluationGovernanceService

        class Repository:
            async def load_report_for_tenant(self, **kwargs):
                return {"metrics": {"answer_term_f1": 1.0}}
            async def save_gate_evidence(self, evidence):
                raise AssertionError("must not persist")
        async def run():
            service = EvaluationGovernanceService(Repository())
            with self.assertRaises(ValueError):
                await service.decide_release(
                    tenant_id="tenant-a", candidate_run_id="run",
                    policy=EvaluationThresholdPolicy("prod", 1, {"answer_term_f1": 0.9}, {}),
                    evidence_refs=(),
                )
        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
