from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent_platform.quotas.concurrency import (
    QuotaConcurrencyLimiter, QuotaReservationMaintenanceWorker,
)
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


    async def test_slot_renews_postgres_style_reservation_lease(self):
        class LeaseAwareStore(InMemoryQuotaStore):
            def __init__(self):
                super().__init__()
                self.heartbeats = 0

            async def heartbeat_reservation(self, reservation_id, *, lease_seconds=300):
                self.heartbeats += 1
                return True

        store = LeaseAwareStore()
        service = QuotaService(store)
        now = datetime.now(timezone.utc)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="queue", scope_id="priority",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(hours=1), hard_concurrency=1,
        ))
        limiter = QuotaConcurrencyLimiter(
            service, lease_seconds=15, heartbeat_interval_seconds=0.01,
        )
        async with limiter.slot(
            tenant_id="tenant-a", run_id=str(uuid4()),
            scope_type="queue", scope_id="priority",
        ):
            await asyncio.sleep(0.035)
        self.assertGreaterEqual(store.heartbeats, 2)

    async def test_maintenance_worker_reclaims_stale_reservations(self):
        class RecoverableStore(InMemoryQuotaStore):
            async def expire_stale_reservations(self, *, limit=500):
                self.last_limit = limit
                return 3

        store = RecoverableStore()
        worker = QuotaReservationMaintenanceWorker(
            QuotaService(store), poll_interval_seconds=1, batch_size=25,
        )
        self.assertEqual(await worker.run_once(), 3)
        self.assertEqual(store.last_limit, 25)
        await worker.start()
        self.assertTrue(worker.running)
        await worker.stop()
        self.assertFalse(worker.running)


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
