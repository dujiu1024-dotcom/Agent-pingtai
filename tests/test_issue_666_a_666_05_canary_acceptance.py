from __future__ import annotations

import unittest

from agent_platform.data_governance.cutover import (
    ConsistencyEvidence,
    CutoverPhase,
    CutoverPolicy,
    choose_read_plan,
    evaluate_cutover,
    plan_write,
)


def healthy_evidence(**overrides):
    values = {
        "sampled_rows": 10_000,
        "mismatched_rows": 0,
        "source_checksum": "sha256:identical-redacted-fixture",
        "target_checksum": "sha256:identical-redacted-fixture",
        "replication_lag_seconds": 0.2,
        "write_error_rate": 0.0,
        "observed_seconds": 900.0,
        "legacy_write_rate": 1.0,
        "evidence_age_seconds": 1.0,
    }
    values.update(overrides)
    return ConsistencyEvidence(**values)


class Issue666A66605CanaryAcceptanceTests(unittest.TestCase):
    def test_ten_percent_canary_is_stable_and_within_distribution_tolerance(self):
        policy = CutoverPolicy(min_sampled_rows=10_000, min_observation_seconds=900)
        evidence = healthy_evidence()
        dual_write = evaluate_cutover(
            current=CutoverPhase.LEGACY_PRIMARY,
            requested=CutoverPhase.DUAL_WRITE,
            evidence=evidence,
            policy=policy,
        )
        self.assertTrue(dual_write.allowed, dual_write.reasons)
        canary = evaluate_cutover(
            current=dual_write.phase,
            requested=CutoverPhase.CANARY,
            evidence=evidence,
            policy=policy,
        )
        self.assertTrue(canary.allowed, canary.reasons)

        assignments = []
        for tenant_number in range(100):
            tenant_id = f"tenant-{tenant_number:03d}"
            for request_number in range(100):
                key = f"request-{request_number:03d}"
                first = choose_read_plan(
                    phase=canary.phase, tenant_id=tenant_id,
                    request_key=key, canary_percent=10,
                )
                retry = choose_read_plan(
                    phase=canary.phase, tenant_id=tenant_id,
                    request_key=key, canary_percent=10,
                )
                self.assertEqual(first, retry, "retry must retain its route")
                self.assertIn(first.primary_system, {"legacy", "target"})
                self.assertIn(first.shadow_system, {"legacy", "target"})
                self.assertNotEqual(first.primary_system, first.shadow_system)
                assignments.append(first.primary_system)

        target_count = assignments.count("target")
        self.assertGreaterEqual(target_count, 800)
        self.assertLessEqual(target_count, 1_200)
        self.assertEqual(len(assignments), 10_000)

    def test_healthy_canary_keeps_write_authority_and_shadow_replication_explicit(self):
        plan = plan_write(phase=CutoverPhase.CANARY)
        self.assertEqual(plan.authoritative_system, "target")
        self.assertEqual(plan.write_systems, ("target",))
        self.assertEqual(plan.replication_targets, ("legacy",))

    def test_consistency_or_write_error_anomaly_blocks_canary_and_rolls_back_active_canary(self):
        policy = CutoverPolicy(min_sampled_rows=100)
        bad = healthy_evidence(
            sampled_rows=1_000,
            mismatched_rows=2,
            source_checksum="source-checksum",
            target_checksum="target-checksum",
            write_error_rate=0.02,
        )
        advancing = evaluate_cutover(
            current=CutoverPhase.DUAL_WRITE,
            requested=CutoverPhase.CANARY,
            evidence=bad,
            policy=policy,
        )
        self.assertFalse(advancing.allowed)
        self.assertIn("row_mismatch_threshold_exceeded", advancing.reasons)
        self.assertIn("checksum_mismatch", advancing.reasons)
        self.assertIn("write_error_threshold_exceeded", advancing.reasons)

        active = evaluate_cutover(
            current=CutoverPhase.CANARY,
            requested=CutoverPhase.CANARY,
            evidence=bad,
            policy=policy,
        )
        self.assertEqual(active.phase, CutoverPhase.ROLLED_BACK)
        self.assertTrue(active.rollback_required)

    def test_stale_or_insufficient_observation_evidence_fails_closed(self):
        policy = CutoverPolicy(min_sampled_rows=100, max_evidence_age_seconds=60)
        for bad_evidence in (
            healthy_evidence(sampled_rows=10),
            healthy_evidence(evidence_age_seconds=61),
            healthy_evidence(observed_seconds=899),
        ):
            decision = evaluate_cutover(
                current=CutoverPhase.DUAL_WRITE,
                requested=CutoverPhase.CANARY,
                evidence=bad_evidence,
                policy=policy,
            )
            self.assertFalse(decision.allowed)
            self.assertNotEqual(decision.phase, CutoverPhase.CANARY)


if __name__ == "__main__":
    unittest.main()
