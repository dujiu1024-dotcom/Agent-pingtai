from __future__ import annotations

import unittest

from agent_platform.data_governance.cutover import (
    ConsistencyEvidence,
    CutoverPhase,
    CutoverPolicy,
    choose_read_plan,
    evaluate_consistency,
    evaluate_cutover,
    plan_write,
    rollback_required,
)


def evidence(**overrides):
    values = {
        "sampled_rows": 1000,
        "mismatched_rows": 0,
        "source_checksum": "same",
        "target_checksum": "same",
        "replication_lag_seconds": 1.0,
        "write_error_rate": 0.0,
        "observed_seconds": 1800.0,
        "legacy_write_rate": 0.0,
        "evidence_age_seconds": 0.0,
    }
    values.update(overrides)
    return ConsistencyEvidence(**values)


class MigrationCutoverTests(unittest.TestCase):
    def test_dual_write_keeps_legacy_authoritative_until_switch(self):
        plan = plan_write(phase=CutoverPhase.DUAL_WRITE)
        self.assertEqual(plan.authoritative_system, "legacy")
        self.assertEqual(plan.write_systems, ("legacy",))
        self.assertEqual(plan.replication_targets, ("target",))

    def test_target_primary_writes_both_during_rollback_window(self):
        plan = plan_write(phase=CutoverPhase.TARGET_PRIMARY)
        self.assertEqual(plan.authoritative_system, "target")
        self.assertEqual(plan.write_systems, ("target",))
        self.assertEqual(plan.replication_targets, ("legacy",))

    def test_canary_assignment_is_stable_for_same_tenant_and_request(self):
        first = choose_read_plan(
            phase=CutoverPhase.CANARY, tenant_id="t1", request_key="r1",
            canary_percent=35,
        )
        second = choose_read_plan(
            phase=CutoverPhase.CANARY, tenant_id="t1", request_key="r1",
            canary_percent=35,
        )
        self.assertEqual(first, second)
        self.assertIn(first.primary_system, {"legacy", "target"})
        self.assertEqual(first.shadow_system, "target" if first.primary_system == "legacy" else "legacy")

    def test_canary_percentage_validation(self):
        with self.assertRaises(ValueError):
            choose_read_plan(
                phase=CutoverPhase.CANARY, tenant_id="t1", request_key="r1",
                canary_percent=101,
            )

    def test_consistency_rejects_checksum_and_lag_drift(self):
        failures = evaluate_consistency(
            evidence(target_checksum="different", replication_lag_seconds=9),
            CutoverPolicy(),
        )
        self.assertIn("checksum_mismatch", failures)
        self.assertIn("replication_lag_threshold_exceeded", failures)

    def test_unsafe_dual_write_automatically_rolls_back(self):
        decision = evaluate_cutover(
            current=CutoverPhase.DUAL_WRITE,
            requested=CutoverPhase.CANARY,
            evidence=evidence(mismatched_rows=10),
            policy=CutoverPolicy(),
        )
        self.assertEqual(decision.phase, CutoverPhase.ROLLED_BACK)
        self.assertTrue(decision.rollback_required)
        self.assertIn("row_mismatch_threshold_exceeded", decision.reasons)

    def test_canary_requires_observation_window(self):
        decision = evaluate_cutover(
            current=CutoverPhase.DUAL_WRITE,
            requested=CutoverPhase.CANARY,
            evidence=evidence(observed_seconds=30),
            policy=CutoverPolicy(min_observation_seconds=600),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("observation_window_not_met", decision.reasons)

    def test_target_primary_requires_valid_canary_evidence(self):
        decision = evaluate_cutover(
            current=CutoverPhase.CANARY,
            requested=CutoverPhase.TARGET_PRIMARY,
            evidence=evidence(write_error_rate=0.2),
            policy=CutoverPolicy(),
        )
        self.assertEqual(decision.phase, CutoverPhase.ROLLED_BACK)
        self.assertTrue(decision.rollback_required)

    def test_legacy_retirement_requires_zero_legacy_writes_and_elapsed_window(self):
        policy = CutoverPolicy(rollback_window_seconds=86400)
        not_ready = evaluate_cutover(
            current=CutoverPhase.TARGET_PRIMARY,
            requested=CutoverPhase.RETIRED,
            evidence=evidence(legacy_write_rate=0.001, observed_seconds=90000),
            policy=policy,
        )
        self.assertFalse(not_ready.allowed)
        self.assertIn("legacy_writes_still_observed", not_ready.reasons)
        self.assertEqual(plan_write(phase=CutoverPhase.TARGET_PRIMARY).write_systems,
                         ("target",))

        ready = evaluate_cutover(
            current=CutoverPhase.TARGET_PRIMARY,
            requested=CutoverPhase.RETIRED,
            evidence=evidence(observed_seconds=90000),
            policy=policy,
        )
        self.assertTrue(ready.allowed)
        self.assertEqual(ready.phase, CutoverPhase.RETIRED)

    def test_invalid_phase_transition_is_rejected(self):
        decision = evaluate_cutover(
            current=CutoverPhase.LEGACY_PRIMARY,
            requested=CutoverPhase.RETIRED,
            evidence=evidence(),
            policy=CutoverPolicy(),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("invalid_phase_transition", decision.reasons)

    def test_rollback_guard_is_fail_closed_for_insufficient_samples(self):
        self.assertTrue(rollback_required(
            evidence=evidence(sampled_rows=0), policy=CutoverPolicy(),
        ))


    def test_stale_evidence_blocks_cutover_advancement(self):
        decision = evaluate_cutover(
            current=CutoverPhase.DUAL_WRITE,
            requested=CutoverPhase.CANARY,
            evidence=evidence(evidence_age_seconds=61),
            policy=CutoverPolicy(max_evidence_age_seconds=60),
        )
        self.assertFalse(decision.allowed)
        self.assertEqual(decision.phase, CutoverPhase.DUAL_WRITE)
        self.assertIn("evidence_stale", decision.reasons)

    def test_stale_evidence_during_canary_requires_rollback(self):
        decision = evaluate_cutover(
            current=CutoverPhase.CANARY,
            requested=CutoverPhase.TARGET_PRIMARY,
            evidence=evidence(evidence_age_seconds=61),
            policy=CutoverPolicy(max_evidence_age_seconds=60),
        )
        self.assertEqual(decision.phase, CutoverPhase.ROLLED_BACK)
        self.assertTrue(decision.rollback_required)
        self.assertIn("evidence_stale", decision.reasons)

    def test_evidence_age_must_be_finite_and_non_negative(self):
        with self.assertRaises(ValueError):
            evidence(evidence_age_seconds=-1)
        with self.assertRaises(ValueError):
            evidence(evidence_age_seconds=float("nan"))

    def test_invalid_metric_values_are_rejected(self):
        with self.assertRaises(ValueError):
            evidence(write_error_rate=1.5)


if __name__ == "__main__":
    unittest.main()
