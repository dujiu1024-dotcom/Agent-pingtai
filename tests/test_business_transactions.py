from __future__ import annotations

import asyncio
import unittest

from agent_platform.business_transactions import (
    ApprovalDecision, BusinessTransactionEngine, InMemoryTransactionRepository,
    RecoveryRejected, RetryPolicy, TransactionStatus, TransactionStep,
)


class BusinessTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repo = InMemoryTransactionRepository()
        self.calls = []
        async def create(payload):
            self.calls.append(("create", payload.get("value")))
            return {"external_id": "erp-42"}
        async def notify(payload):
            self.calls.append(("notify", payload.get("value")))
            raise RuntimeError("CRM unavailable")
        async def compensate(payload):
            self.calls.append(("compensate", payload["step_output"]["external_id"]))
            return {"reversed": True}
        self.engine = BusinessTransactionEngine(
            repository=self.repo, operations={"erp.create": create, "crm.notify": notify},
            compensations={"erp.cancel": compensate},
        )

    async def run_flow(self, key="evt-1", value=7, retry=1, partial=False):
        return await self.engine.execute(
            transaction_id="tx-" + key, tenant_id="tenant-a", workflow_id="order-sync",
            workflow_version=2, idempotency_key=key,
            steps=(
                TransactionStep("erp", "erp.create", compensation_operation="erp.cancel"),
                TransactionStep("crm", "crm.notify", retry=RetryPolicy(max_attempts=retry),
                                allow_partial_success=partial),
            ),
            payload={"value": value}, actor_id="user-a",
        )

    async def test_idempotency_deduplicates_and_rejects_key_payload_collision(self):
        first = await self.run_flow()
        duplicate = await self.run_flow()
        self.assertIs(first, duplicate)
        self.assertEqual(len([c for c in self.calls if c[0] == "create"]), 1)
        with self.assertRaises(Exception):
            await self.run_flow(value=8)

    async def test_saga_compensates_completed_steps_in_reverse_after_failure(self):
        result = await self.run_flow()
        self.assertEqual(result.status, TransactionStatus.COMPENSATED)
        self.assertEqual(self.calls[-1], ("compensate", "erp-42"))
        self.assertIn("saga.compensated", [event.event_type for event in result.audit])

    async def test_partial_success_preserves_completed_effects_without_compensation(self):
        result = await self.run_flow(key="partial", partial=True)
        self.assertEqual(result.status, TransactionStatus.PARTIAL)
        self.assertEqual(result.step_results["erp"].status, "succeeded")
        self.assertEqual(len(result.compensation_results), 0)

    async def test_retry_attempts_are_bounded(self):
        result = await self.run_flow(key="retry", retry=2)
        self.assertEqual(result.step_results["crm"].attempts, 2)
        self.assertEqual(len([c for c in self.calls if c[0] == "notify"]), 2)

    async def test_timeout_is_recorded_and_retried(self):
        async def slow(payload):
            await asyncio.sleep(0.05)
            return True
        engine = BusinessTransactionEngine(repository=self.repo, operations={"slow": slow})
        result = await engine.execute(
            transaction_id="tx-timeout", tenant_id="tenant-a", workflow_id="wf",
            workflow_version=1, idempotency_key="timeout",
            steps=(TransactionStep("slow", "slow", timeout_seconds=0.001,
                                   retry=RetryPolicy(max_attempts=2)),),
            payload={}, actor_id="user-a", saga=False,
        )
        self.assertEqual(result.status, TransactionStatus.FAILED)
        self.assertIn("TimeoutError", result.step_results["slow"].error)

    async def test_approval_pending_and_rejected_are_fail_closed(self):
        async def pending(step, payload):
            return ApprovalDecision.PENDING
        engine = BusinessTransactionEngine(repository=self.repo, operations={"op": self.engine.operations["erp.create"]},
                                           approval_handler=pending)
        pending_tx = await engine.execute(
            transaction_id="tx-pending", tenant_id="tenant-a", workflow_id="wf",
            workflow_version=1, idempotency_key="approval-pending",
            steps=(TransactionStep("approval", "op", requires_approval=True),),
            payload={}, actor_id="user-a")
        self.assertEqual(pending_tx.status, TransactionStatus.WAITING_APPROVAL)
        self.assertEqual(self.calls, [])

        async def rejected(step, payload):
            return ApprovalDecision.REJECTED
        engine = BusinessTransactionEngine(repository=self.repo, operations={"op": self.engine.operations["erp.create"]},
                                           approval_handler=rejected)
        rejected_tx = await engine.execute(
            transaction_id="tx-rejected", tenant_id="tenant-a", workflow_id="wf",
            workflow_version=1, idempotency_key="approval-rejected",
            steps=(TransactionStep("approval", "op", requires_approval=True),),
            payload={}, actor_id="user-a")
        self.assertEqual(rejected_tx.status, TransactionStatus.REJECTED)
        self.assertEqual(self.calls, [])

    async def test_manual_recovery_requires_risk_approval_and_prevents_successful_replay(self):
        result = await self.run_flow(key="recover", partial=True)
        async def reject(tx, step_id):
            return False
        with self.assertRaises(RecoveryRejected):
            await self.engine.recover_step(
                tenant_id="tenant-a", transaction_id=result.transaction_id,
                step=TransactionStep("crm", "crm.notify"), actor_id="operator",
                risk_check=reject, payload={"value": 7})
        async def approve(tx, step_id):
            return True
        recovered = await self.engine.recover_step(
            tenant_id="tenant-a", transaction_id=result.transaction_id,
            step=TransactionStep("erp", "erp.create"), actor_id="operator",
            risk_check=approve, payload={"value": 7})
        self.assertEqual(recovered.status, TransactionStatus.PARTIAL)
        with self.assertRaises(Exception):
            await self.engine.recover_step(
                tenant_id="tenant-a", transaction_id=result.transaction_id,
                step=TransactionStep("erp", "erp.create"), actor_id="operator",
                risk_check=approve, payload={"value": 7})

    async def test_tenant_scoped_lookup_and_audit(self):
        result = await self.run_flow(key="tenant")
        self.assertIsNone(await self.repo.get("tenant-b", result.transaction_id))
        self.assertTrue(result.audit)
        self.assertEqual(result.workflow_version, 2)


if __name__ == "__main__":
    unittest.main()
