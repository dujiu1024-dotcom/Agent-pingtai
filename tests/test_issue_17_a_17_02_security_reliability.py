from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from agent_platform.quotas.service import (
    Budget,
    BudgetExceeded,
    InMemoryQuotaStore,
    QuotaService,
    UsageEvent,
)


class QuotaSecurityReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.start = self.now - timedelta(minutes=1)
        self.end = self.now + timedelta(hours=1)

    async def _service(self, tenant_id: str, *, concurrency: int = 10):
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id=tenant_id,
            scope_type="tenant",
            scope_id=tenant_id,
            period_start=self.start,
            period_end=self.end,
            hard_tokens=10_000,
            hard_cost_usd=Decimal("10"),
            hard_concurrency=concurrency,
        ))
        return service, store

    async def test_tenant_budgets_and_usage_are_isolated(self):
        service_a, store_a = await self._service("tenant-a")
        service_b, store_b = await self._service("tenant-b")
        reservation_a, _ = await service_a.reserve_run(
            tenant_id="tenant-a", run_id="run-a", scope_type="tenant",
            scope_id="tenant-a", estimated_tokens=10,
            estimated_cost_usd=Decimal("0.1"),
        )
        with self.assertRaises(BudgetExceeded):
            await service_b.reserve_run(
                tenant_id="tenant-a", run_id="run-cross-tenant",
                scope_type="tenant", scope_id="tenant-a",
                estimated_tokens=10, estimated_cost_usd=Decimal("0.1"),
            )
        event = UsageEvent(
            event_id=str(uuid4()), tenant_id="tenant-a", run_id="run-a",
            model_key="model@1", input_tokens=4, output_tokens=2,
            cost_usd=Decimal("0.01"), occurred_at=self.now,
        )
        await store_a.settle(reservation_a.reservation_id, event)
        self.assertEqual(
            await store_a.usage("tenant-a", "tenant", "tenant-a", self.start, self.end),
            (6, Decimal("0.01"), 0),
        )
        self.assertEqual(
            await store_b.usage("tenant-b", "tenant", "tenant-b", self.start, self.end),
            (0, Decimal("0"), 0),
        )

    async def test_settlement_rejects_cross_tenant_or_cross_run_event(self):
        service, store = await self._service("tenant-a")
        reservation, _ = await service.reserve_run(
            tenant_id="tenant-a", run_id="run-a", scope_type="tenant",
            scope_id="tenant-a", estimated_tokens=10,
            estimated_cost_usd=Decimal("0.1"),
        )
        for tenant_id, run_id in (("tenant-b", "run-a"), ("tenant-a", "run-b")):
            event = UsageEvent(
                event_id=str(uuid4()), tenant_id=tenant_id, run_id=run_id,
                model_key="model@1", input_tokens=1, output_tokens=1,
                cost_usd=Decimal("0.01"), occurred_at=self.now,
            )
            with self.assertRaises(PermissionError):
                await store.settle(reservation.reservation_id, event)
        self.assertEqual(
            await store.usage("tenant-a", "tenant", "tenant-a", self.start, self.end),
            (10, Decimal("0.1"), 1),
        )

    async def test_active_run_reservation_rejects_duplicate_retry_then_release_allows_retry(self):
        service, store = await self._service("tenant-a")
        first, _ = await service.reserve_run(
            tenant_id="tenant-a", run_id="run-retry", scope_type="tenant",
            scope_id="tenant-a", estimated_tokens=10,
            estimated_cost_usd=Decimal("0.1"),
        )
        with self.assertRaisesRegex(ValueError, "active reservation"):
            await service.reserve_run(
                tenant_id="tenant-a", run_id="run-retry", scope_type="tenant",
                scope_id="tenant-a", estimated_tokens=10,
                estimated_cost_usd=Decimal("0.1"),
            )
        self.assertTrue(await store.release(first.reservation_id))
        retry, _ = await service.reserve_run(
            tenant_id="tenant-a", run_id="run-retry", scope_type="tenant",
            scope_id="tenant-a", estimated_tokens=10,
            estimated_cost_usd=Decimal("0.1"),
        )
        self.assertNotEqual(first.reservation_id, retry.reservation_id)

    async def test_concurrent_reservations_never_exceed_tenant_limit(self):
        service, _store = await self._service("tenant-a", concurrency=2)

        async def reserve(index: int):
            try:
                return await service.reserve_run(
                    tenant_id="tenant-a", run_id=f"run-{index}",
                    scope_type="tenant", scope_id="tenant-a",
                    estimated_tokens=1, estimated_cost_usd=Decimal("0.001"),
                )
            except BudgetExceeded:
                return None

        results = await asyncio.gather(*(reserve(index) for index in range(12)))
        self.assertEqual(sum(result is not None for result in results), 2)

    async def test_store_unavailable_fails_closed_without_fallback(self):
        class UnavailableStore(InMemoryQuotaStore):
            def __init__(self):
                super().__init__()
                self.attempts = 0

            async def reserve(self, reservation):
                self.attempts += 1
                raise ConnectionError("quota database unavailable")

        store = UnavailableStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=self.start, period_end=self.end,
            hard_tokens=100, hard_cost_usd=Decimal("1"),
        ))
        with self.assertRaisesRegex(ConnectionError, "quota database unavailable"):
            await service.reserve_run(
                tenant_id="tenant-a", run_id="run-fail-closed",
                scope_type="tenant", scope_id="tenant-a",
                estimated_tokens=1, estimated_cost_usd=Decimal("0.01"),
            )
        self.assertEqual(store.attempts, 1)


if __name__ == "__main__":
    unittest.main()
