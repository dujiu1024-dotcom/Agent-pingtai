from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agent_platform.quotas import Budget, BudgetExceeded, InMemoryQuotaStore, QuotaService, UsageEvent


class QuotaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.start = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.end = self.start + timedelta(days=1)
        self.budget = Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=self.start, period_end=self.end,
            hard_tokens=100, hard_cost_usd=Decimal("1.00"), hard_concurrency=1,
            soft_tokens=80,
        )
        self.store = InMemoryQuotaStore()
        self.service = QuotaService(self.store)
        await self.service.configure(self.budget)

    async def reserve(self, run_id: str, tokens: int, cost: str):
        return await self.service.reserve_run(
            tenant_id="tenant-a", run_id=run_id, scope_type="tenant", scope_id="tenant-a",
            estimated_tokens=tokens, estimated_cost_usd=Decimal(cost),
        )

    async def test_server_reservation_enforces_hard_tokens_and_concurrency(self) -> None:
        first, warnings = await self.reserve("run-1", 80, "0.50")
        self.assertIn("soft token budget exceeded", warnings)
        with self.assertRaises(BudgetExceeded):
            await self.reserve("run-2", 1, "0.01")
        self.assertTrue(await self.service.release_run(first))
        second, _ = await self.reserve("run-2", 100, "1.00")
        self.assertEqual(second.run_id, "run-2")

    async def test_atomic_reservations_prevent_concurrent_overbooking(self) -> None:
        async def attempt(run_id: str):
            try:
                return await self.reserve(run_id, 60, "0.60")
            except BudgetExceeded:
                return None
        results = await asyncio.gather(attempt("run-1"), attempt("run-2"))
        self.assertEqual(sum(result is not None for result in results), 1)

    async def test_settlement_is_tenant_run_bound_and_releases_reservation(self) -> None:
        reservation, _ = await self.reserve("run-1", 90, "0.90")
        with self.assertRaises(PermissionError):
            await self.store.settle(
                reservation.reservation_id,
                UsageEvent("e1", "tenant-b", "run-1", "model-x", 1, 1, Decimal("0.1"), self.start),
            )
        event = await self.service.settle_run(
            reservation, model_key="model-x", input_tokens=30, output_tokens=20,
            cost_usd=Decimal("0.25"), occurred_at=self.start,
        )
        self.assertEqual(event.total_tokens, 50)
        tokens, cost, concurrency = await self.store.usage(
            "tenant-a", "tenant", "tenant-a", self.start, self.end,
        )
        self.assertEqual((tokens, cost, concurrency), (50, Decimal("0.25"), 0))

    async def test_unconfigured_scope_and_tenant_mismatch_rejected(self) -> None:
        with self.assertRaises(BudgetExceeded):
            await self.service.reserve_run(
                tenant_id="tenant-b", run_id="run-1", scope_type="tenant", scope_id="tenant-b",
                estimated_tokens=1, estimated_cost_usd=Decimal("0.01"),
            )
        with self.assertRaises(BudgetExceeded):
            await self.service.reserve_run(
                tenant_id="tenant-a", run_id="run-1", scope_type="agent", scope_id="agent-x",
                estimated_tokens=1, estimated_cost_usd=Decimal("0.01"),
            )

    async def test_negative_usage_rejected(self) -> None:
        reservation, _ = await self.reserve("run-1", 10, "0.10")
        with self.assertRaises(ValueError):
            await self.service.settle_run(
                reservation, model_key="model-x", input_tokens=-1, output_tokens=1,
                cost_usd=Decimal("0.01"),
            )
