from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agent_platform.quotas.service import (
    Budget,
    BudgetExceeded,
    InMemoryQuotaStore,
    QuotaService,
)


class QuotaConcurrentDeductionTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_reservations_never_overbook_any_hard_quota(self) -> None:
        now = datetime.now(timezone.utc)
        period_start = now - timedelta(minutes=1)
        period_end = now + timedelta(hours=1)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-race",
            scope_type="tenant",
            scope_id="tenant-race",
            period_start=period_start,
            period_end=period_end,
            hard_tokens=100,
            hard_cost_usd=Decimal("0.95"),
            hard_concurrency=3,
        ))

        start = asyncio.Event()

        async def reserve(index: int):
            await start.wait()
            try:
                return await service.reserve_run(
                    tenant_id="tenant-race",
                    run_id=f"run-race-{index}",
                    scope_type="tenant",
                    scope_id="tenant-race",
                    estimated_tokens=30,
                    estimated_cost_usd=Decimal("0.30"),
                )
            except BudgetExceeded:
                return None

        attempts = [
            asyncio.create_task(reserve(index))
            for index in range(24)
        ]
        start.set()
        results = await asyncio.gather(*attempts)

        accepted = [result for result in results if result is not None]
        rejected = [result for result in results if result is None]
        self.assertEqual(len(accepted), 3)
        self.assertEqual(len(rejected), 21)

        tokens, cost, concurrency = await store.usage(
            "tenant-race", "tenant", "tenant-race", period_start, period_end,
        )
        self.assertEqual(tokens, 90)
        self.assertEqual(cost, Decimal("0.90"))
        self.assertEqual(concurrency, 3)

        # Releasing a reservation makes exactly one slot available to a later request.
        reservation = accepted[0][0]
        self.assertTrue(await service.release_run(reservation))
        replacement, _ = await service.reserve_run(
            tenant_id="tenant-race",
            run_id="run-race-replacement",
            scope_type="tenant",
            scope_id="tenant-race",
            estimated_tokens=10,
            estimated_cost_usd=Decimal("0.05"),
        )
        self.assertEqual(replacement.run_id, "run-race-replacement")
        tokens, cost, concurrency = await store.usage(
            "tenant-race", "tenant", "tenant-race", period_start, period_end,
        )
        self.assertEqual((tokens, cost, concurrency), (70, Decimal("0.65"), 3))


if __name__ == "__main__":
    unittest.main()
