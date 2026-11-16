from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter
from agent_platform.quotas.service import (
    Budget, BudgetExceeded, InMemoryQuotaStore, QuotaService,
)


class QuotaConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_queue_concurrency_is_shared_and_released_on_context_exit(self):
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        now = datetime.now(timezone.utc)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="queue", scope_id="priority",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(hours=1), hard_concurrency=1,
        ))
        limiter = QuotaConcurrencyLimiter(service)
        first = await limiter.acquire(
            tenant_id="tenant-a", run_id=str(uuid4()),
            scope_type="queue", scope_id="priority",
        )
        with self.assertRaises(BudgetExceeded):
            await limiter.acquire(
                tenant_id="tenant-a", run_id=str(uuid4()),
                scope_type="queue", scope_id="priority",
            )
        self.assertTrue(await limiter.release(first))
        second = await limiter.acquire(
            tenant_id="tenant-a", run_id=str(uuid4()),
            scope_type="queue", scope_id="priority",
        )
        self.assertTrue(await limiter.release(second))

    async def test_worker_pool_slot_releases_after_exception(self):
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        now = datetime.now(timezone.utc)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="worker_pool", scope_id="gpu",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(hours=1), hard_concurrency=1,
        ))
        limiter = QuotaConcurrencyLimiter(service)
        with self.assertRaisesRegex(RuntimeError, "worker failed"):
            async with limiter.slot(
                tenant_id="tenant-a", run_id=str(uuid4()),
                scope_type="worker_pool", scope_id="gpu",
            ):
                raise RuntimeError("worker failed")
        reservation = await limiter.acquire(
            tenant_id="tenant-a", run_id=str(uuid4()),
            scope_type="worker_pool", scope_id="gpu",
        )
        self.assertTrue(await limiter.release(reservation))

    async def test_queue_budget_must_be_concurrency_only(self):
        now = datetime.now(timezone.utc)
        with self.assertRaises(ValueError):
            Budget(
                tenant_id="tenant-a", scope_type="queue", scope_id="default",
                period_start=now, period_end=now + timedelta(hours=1),
                hard_tokens=100, hard_concurrency=2,
            )
        with self.assertRaises(ValueError):
            Budget(
                tenant_id="tenant-a", scope_type="worker_pool", scope_id="gpu",
                period_start=now, period_end=now + timedelta(hours=1),
            )


if __name__ == "__main__":
    unittest.main()
