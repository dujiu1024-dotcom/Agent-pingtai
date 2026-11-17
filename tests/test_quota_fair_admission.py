from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agent_platform.quotas.admission import (
    AdmissionRequest, TenantFairQuotaAdmissionController,
)
from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter
from agent_platform.quotas.service import (
    Budget, BudgetExceeded, InMemoryQuotaStore, QuotaService,
)


class FairQuotaAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryQuotaStore()
        self.service = QuotaService(self.store)
        self.limiter = QuotaConcurrencyLimiter(self.service)
        self.admission = TenantFairQuotaAdmissionController(self.limiter)
        now = datetime.now(timezone.utc)
        for tenant in ("tenant-a", "tenant-b"):
            await self.service.configure(Budget(
                tenant_id=tenant, scope_type="queue", scope_id="shared",
                period_start=now - timedelta(minutes=1),
                period_end=now + timedelta(hours=1), hard_concurrency=1,
            ))

    async def request(self, tenant, strategy="queue", timeout=2):
        return await self.admission.acquire(AdmissionRequest(
            tenant_id=tenant, run_id=str(uuid4()), scope_type="queue",
            scope_id="shared", strategy=strategy,
            wait_timeout_seconds=timeout if strategy == "queue" else None,
        ))

    async def test_reject_policy_fails_immediately_when_full(self):
        first = await self.request("tenant-a", strategy="reject")
        with self.assertRaises(BudgetExceeded):
            await self.request("tenant-a", strategy="reject")
        await self.admission.release(first)

    async def test_saturated_tenant_does_not_block_another_tenant(self):
        first_a = await self.request("tenant-a", strategy="reject")
        waiting_a = asyncio.create_task(self.request("tenant-a"))
        await asyncio.sleep(0)
        second_b = await asyncio.wait_for(self.request("tenant-b"), timeout=1)
        self.assertEqual(second_b.tenant_id, "tenant-b")
        await self.admission.release(second_b)
        await self.admission.release(first_a)
        second_a = await asyncio.wait_for(waiting_a, timeout=1)
        self.assertEqual(second_a.tenant_id, "tenant-a")
        await self.admission.release(second_a)

    async def test_fifo_order_within_same_tenant(self):
        first = await self.request("tenant-a", strategy="reject")
        waiting_one = asyncio.create_task(self.request("tenant-a"))
        await asyncio.sleep(0)
        waiting_two = asyncio.create_task(self.request("tenant-a"))
        await asyncio.sleep(0)
        await self.admission.release(first)
        granted_one = await asyncio.wait_for(waiting_one, timeout=1)
        with self.assertRaises(asyncio.TimeoutError):
            await asyncio.wait_for(asyncio.shield(waiting_two), timeout=0.02)
        await self.admission.release(granted_one)
        granted_two = await asyncio.wait_for(waiting_two, timeout=1)
        await self.admission.release(granted_two)

    async def test_queue_timeout_and_cancellation_remove_waiters(self):
        first = await self.request("tenant-a", strategy="reject")
        with self.assertRaises(BudgetExceeded):
            await self.request("tenant-a", timeout=0.02)
        cancelled = asyncio.create_task(self.request("tenant-a", timeout=2))
        await asyncio.sleep(0)
        cancelled.cancel()
        await asyncio.gather(cancelled, return_exceptions=True)
        await self.admission.release(first)
        final = await self.request("tenant-a", strategy="reject")
        await self.admission.release(final)


if __name__ == "__main__":
    unittest.main()
