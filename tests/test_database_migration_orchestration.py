import unittest

from agent_platform.deployment.migrations import (
    DualWriteCoordinator, MigrationError, MigrationPhase, MigrationPlan,
    MigrationRun, SchemaCompatibility, verify_compatibility,
)


class MigrationOrchestrationTests(unittest.TestCase):
    def plan(self):
        forward = SchemaCompatibility(2, 1, 2, 1, 2)
        rollback = SchemaCompatibility(2, 1, 2, 1, 1)
        return MigrationPlan("users-v2", 1, 2, 1, 2, forward, rollback)

    def test_expand_dual_write_backfill_validate_cutover_contract(self):
        run = MigrationRun(self.plan())
        for phase in (MigrationPhase.EXPANDED, MigrationPhase.DUAL_WRITE,
                      MigrationPhase.BACKFILL):
            run.advance(phase)
        with self.assertRaisesRegex(MigrationError, "digest"):
            run.advance(MigrationPhase.VALIDATED)
        run.advance(MigrationPhase.VALIDATED, validation_digest="sha256:verified")
        run.advance(MigrationPhase.CUTOVER)
        self.assertTrue(run.reads_on_new_schema)
        self.assertTrue(run.old_app_rollback_available)
        run.advance(MigrationPhase.CONTRACTED)
        self.assertFalse(run.old_app_rollback_available)
        self.assertFalse(run.dual_write_enabled)
        with self.assertRaisesRegex(MigrationError, "after contract"):
            run.rollback("late rollback")

    def test_rollback_before_contract_restores_old_app_path(self):
        run = MigrationRun(self.plan())
        run.advance(MigrationPhase.EXPANDED)
        run.advance(MigrationPhase.DUAL_WRITE)
        run.rollback("new writer error")
        self.assertEqual(run.phase, MigrationPhase.ROLLED_BACK)
        self.assertFalse(run.dual_write_enabled)
        self.assertFalse(run.reads_on_new_schema)

    def test_cutover_requires_dual_write_and_validation(self):
        run = MigrationRun(self.plan())
        run.advance(MigrationPhase.EXPANDED)
        run.advance(MigrationPhase.DUAL_WRITE)
        run.advance(MigrationPhase.BACKFILL)
        with self.assertRaises(MigrationError):
            run.advance(MigrationPhase.CUTOVER)

    def test_rejects_incompatible_old_or_new_application(self):
        with self.assertRaisesRegex(ValueError, "old application"):
            MigrationPlan("bad", 1, 2, 1, 2,
                          SchemaCompatibility(2, 2, 2, 2, 2),
                          SchemaCompatibility(2, 1, 2, 1, 1))
        with self.assertRaisesRegex(MigrationError, "incompatible"):
            verify_compatibility(schema=SchemaCompatibility(2, 2, 2, 2, 2),
                                 app_reader=1, app_writer=1)

    def test_dual_write_shadow_failure_is_reconciled(self):
        primary_rows, retries = [], []
        def primary(row):
            primary_rows.append(dict(row))
        def shadow(row):
            raise RuntimeError("shadow unavailable")
        def enqueue(row, error):
            retries.append((dict(row), error))
            return "reconcile-1"
        result = DualWriteCoordinator(primary=primary, shadow=shadow,
                                      enqueue_reconciliation=enqueue).write({"id": "u1"})
        self.assertTrue(result.primary_succeeded)
        self.assertFalse(result.shadow_succeeded)
        self.assertEqual(result.reconciliation_key, "reconcile-1")
        self.assertEqual(len(primary_rows), 1)
        self.assertEqual(len(retries), 1)

    def test_primary_failure_is_not_swallowed_or_written_to_shadow(self):
        shadow_calls = []
        def primary(row):
            raise RuntimeError("primary unavailable")
        coordinator = DualWriteCoordinator(
            primary=primary, shadow=lambda row: shadow_calls.append(row),
            enqueue_reconciliation=lambda row, error: "retry",
        )
        with self.assertRaisesRegex(RuntimeError, "primary unavailable"):
            coordinator.write({"id": "u2"})
        self.assertEqual(shadow_calls, [])

    def test_transition_order_is_enforced(self):
        run = MigrationRun(self.plan())
        with self.assertRaises(MigrationError):
            run.advance(MigrationPhase.CUTOVER)
        with self.assertRaises(MigrationError):
            run.rollback("not started")


if __name__ == "__main__":
    unittest.main()
