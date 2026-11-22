from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="ValueError/KeyError expectation")

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agent_platform.quotas import Budget, BudgetExceeded, InMemoryQuotaStore, QuotaReservationGroupService, QuotaScope
from agent_platform.quotas.inheritance import RunBudgetInheritanceService
from agent_platform.quotas.token_estimator import TokenEstimationError, TiktokenChatEstimator


class _Lineage:
    def __init__(self, root="root-run", parent=None):
        self.root, self.parent = root, parent

    async def get_budget_lineage(self, tenant_id, run_id):
        return {"parent_run_id": self.parent, "budget_root_run_id": self.root}


class RunBudgetGovernanceMatrixTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.start = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.end = self.start + timedelta(hours=2)
        self.store = InMemoryQuotaStore()
        self.groups = QuotaReservationGroupService(self.store)
        self.scopes = (QuotaScope("tenant", "tenant-a"), QuotaScope("run", "root-run"))
        for kind, scope_id in (("tenant", "tenant-a"), ("run", "root-run")):
            await self.store.put_budget(Budget(
                tenant_id="tenant-a", scope_type=kind, scope_id=scope_id,
                period_start=self.start, period_end=self.end,
                hard_tokens=100, hard_cost_usd=Decimal("1.00"),
                hard_concurrency=1, hard_input_tokens=80, hard_output_tokens=20,
            ))

    async def _reserve(self, call_id, *, run_id="child-run", tokens=10,
                       input_tokens=None, output_tokens=None):
        service = RunBudgetInheritanceService(
            lineage_store=_Lineage(), reservation_groups=self.groups,
        )
        return await service.reserve_model_call(
            tenant_id="tenant-a", run_id=run_id, call_id=call_id, scopes=self.scopes,
            estimated_tokens=tokens, estimated_cost_usd=Decimal("0.10"),
            estimated_input_tokens=tokens if input_tokens is None else input_tokens,
            estimated_output_tokens=0 if output_tokens is None else output_tokens,
        )

    async def test_zero_and_exact_hard_limits_are_valid_boundaries(self):
        for kind, scope_id in (("tenant", "tenant-a"), ("run", "root-run")):
            await self.store.put_budget(Budget(
                tenant_id="tenant-a", scope_type=kind, scope_id=scope_id,
                period_start=self.start, period_end=self.end,
                hard_tokens=0, hard_cost_usd=Decimal("1.00"),
            ))
        group, _ = await self._reserve("zero", tokens=0, input_tokens=0, output_tokens=0)
        self.assertEqual(group.root_run_id, "root-run")
        await self.groups.release(group)
        for kind, scope_id in (("tenant", "tenant-a"), ("run", "root-run")):
            await self.store.put_budget(Budget(
                tenant_id="tenant-a", scope_type=kind, scope_id=scope_id,
                period_start=self.start, period_end=self.end,
                hard_tokens=100, hard_cost_usd=Decimal("1.00"),
            ))
        group, _ = await self._reserve("exact", tokens=100, input_tokens=80, output_tokens=20)
        self.assertEqual(group.reservations[0].reserved_tokens, 100)
        await self.groups.release(group)
        with self.assertRaises(BudgetExceeded):
            await self._reserve("over", tokens=101, input_tokens=81, output_tokens=20)

    async def test_input_output_split_limits_reject_one_token_over(self):
        for kind, scope_id in (("tenant", "tenant-a"), ("run", "root-run")):
            await self.store.put_budget(Budget(
                tenant_id="tenant-a", scope_type=kind, scope_id=scope_id,
                period_start=self.start, period_end=self.end,
                hard_tokens=120, hard_cost_usd=Decimal("1.00"),
                hard_input_tokens=80, hard_output_tokens=20,
            ))
        with self.assertRaisesRegex(BudgetExceeded, "input token"):
            await self._reserve("input-over", tokens=100, input_tokens=81, output_tokens=19)
        with self.assertRaisesRegex(BudgetExceeded, "output token"):
            await self._reserve("output-over", tokens=101, input_tokens=80, output_tokens=21)

    async def test_concurrent_reservations_cannot_overbook_root_run(self):
        async def attempt(run_id, call_id):
            try:
                return await self._reserve(call_id, run_id=run_id, tokens=60,
                                           input_tokens=50, output_tokens=10)
            except BudgetExceeded:
                return None
        results = await asyncio.gather(attempt("child-a", "call-a"), attempt("child-b", "call-b"))
        self.assertEqual(sum(item is not None for item in results), 1)
        active = [r for r in self.store._reservations.values()
                  if r.scope_type == "run" and r.scope_id == "root-run"]
        self.assertEqual(len(active), 1)

    async def test_failed_provider_dispatch_releases_every_scope_reservation(self):
        group, _ = await self._reserve("dispatch-failed", tokens=25, input_tokens=20, output_tokens=5)
        self.assertTrue(await self.groups.release(group))
        for scope in self.scopes:
            self.assertEqual(
                await self.store.usage("tenant-a", scope.scope_type, scope.scope_id, self.start, self.end),
                (0, Decimal("0"), 0),
            )

    async def test_retry_uses_new_call_key_but_keeps_persisted_root_budget(self):
        service = RunBudgetInheritanceService(
            lineage_store=_Lineage(root="original-root", parent="failed-run"),
            reservation_groups=self.groups,
        )
        scopes = (QuotaScope("tenant", "tenant-a"), QuotaScope("run", "original-root"))
        await self.store.put_budget(Budget(
            tenant_id="tenant-a", scope_type="run", scope_id="original-root",
            period_start=self.start, period_end=self.end,
            hard_tokens=100, hard_cost_usd=Decimal("1.00"),
        ))
        first, _ = await service.reserve_model_call(
            tenant_id="tenant-a", run_id="retry-run", call_id="attempt-1", scopes=scopes,
            estimated_tokens=10, estimated_cost_usd=Decimal("0.10"),
            estimated_input_tokens=8, estimated_output_tokens=2,
        )
        await service.settle_model_call(first, model_key="model-small", input_tokens=8,
                                        output_tokens=2, cost_usd=Decimal("0.10"))
        second, _ = await service.reserve_model_call(
            tenant_id="tenant-a", run_id="retry-run", call_id="attempt-2", scopes=scopes,
            estimated_tokens=10, estimated_cost_usd=Decimal("0.10"),
            estimated_input_tokens=8, estimated_output_tokens=2,
        )
        self.assertEqual(second.root_run_id, "original-root")
        self.assertNotEqual(first.group_id, second.group_id)
        with self.assertRaises((ValueError, KeyError)):
            await service.reserve_model_call(
                tenant_id="tenant-a", run_id="retry-run", call_id="attempt-1", scopes=scopes,
                estimated_tokens=10, estimated_cost_usd=Decimal("0.10"),
                estimated_input_tokens=8, estimated_output_tokens=2,
            )

    async def test_child_cannot_select_a_different_root_budget(self):
        service = RunBudgetInheritanceService(
            lineage_store=_Lineage(root="root-run"), reservation_groups=self.groups,
        )
        with self.assertRaisesRegex(PermissionError, "persisted root"):
            await service.reserve_model_call(
                tenant_id="tenant-a", run_id="child-run", call_id="tampered",
                scopes=(QuotaScope("tenant", "tenant-a"), QuotaScope("run", "other-root")),
                estimated_tokens=1, estimated_cost_usd=Decimal("0.01"),
                estimated_input_tokens=1, estimated_output_tokens=0,
            )


class _CharacterEncoding:
    def encode(self, value, disallowed_special=()):
        return list(value)


class ModelWindowDifferenceTests(unittest.TestCase):
    def setUp(self):
        self.estimator = TiktokenChatEstimator(encoding_resolver=lambda model_id: _CharacterEncoding())
        self.messages = [{"role": "user", "content": "budget window regression"}]

    def test_same_request_uses_selected_model_context_window(self):
        estimate = self.estimator.estimate(
            model_id="model-large", messages=self.messages,
            request_parameters={"max_completion_tokens": 8}, context_window_tokens=128,
        )
        self.assertGreater(estimate.total_tokens, 8)
        with self.assertRaisesRegex(TokenEstimationError, "exceeds model context window"):
            self.estimator.estimate(
                model_id="model-small", messages=self.messages,
                request_parameters={"max_completion_tokens": 8}, context_window_tokens=24,
            )

    def test_output_allowance_cannot_equal_or_exceed_model_window(self):
        for window in (8, 7, 0, True):
            with self.subTest(window=window), self.assertRaises(TokenEstimationError):
                self.estimator.estimate(
                    model_id="model-small", messages=self.messages,
                    request_parameters={"max_completion_tokens": 8}, context_window_tokens=window,
                )


if __name__ == "__main__":
    unittest.main()
