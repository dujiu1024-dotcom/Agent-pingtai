from __future__ import annotations
import asyncio
import unittest
from agent_platform.quotas.admission import AdmissionRequest, TenantFairQuotaAdmissionController
from agent_platform.quotas.service import BudgetExceeded

class SaturatedLimiter:
    def __init__(self):
        self.attempted = asyncio.Event()
    async def acquire(self, **_kwargs):
        self.attempted.set()
        raise BudgetExceeded("full")

class QuotaAdmissionBoundTests(unittest.IsolatedAsyncioTestCase):
    async def test_global_queue_limit_rejects_additional_tenants(self):
        limiter = SaturatedLimiter()
        controller = TenantFairQuotaAdmissionController(
            limiter, max_pending_total=1, max_pending_per_tenant=1,
        )
        first = asyncio.create_task(controller.acquire(AdmissionRequest(
            tenant_id="tenant-a", run_id="run-a", scope_type="queue",
            scope_id="shared", strategy="queue", wait_timeout_seconds=0.2,
        )))
        await limiter.attempted.wait()
        await asyncio.sleep(0)
        with self.assertRaisesRegex(BudgetExceeded, "queue is full"):
            await controller.acquire(AdmissionRequest(
                tenant_id="tenant-b", run_id="run-b", scope_type="queue",
                scope_id="shared", strategy="queue", wait_timeout_seconds=0.2,
            ))
        with self.assertRaises(BudgetExceeded):
            await first
        self.assertEqual(controller._requests, {})
        self.assertEqual(controller._queues, {})

    async def test_per_tenant_queue_limit_rejects_excess_same_tenant_waiters(self):
        limiter = SaturatedLimiter()
        controller = TenantFairQuotaAdmissionController(
            limiter, max_pending_total=4, max_pending_per_tenant=1,
        )
        first = asyncio.create_task(controller.acquire(AdmissionRequest(
            tenant_id="tenant-a", run_id="run-a", scope_type="queue",
            scope_id="shared", strategy="queue", wait_timeout_seconds=0.2,
        )))
        await limiter.attempted.wait()
        await asyncio.sleep(0)
        with self.assertRaisesRegex(BudgetExceeded, "tenant quota admission queue is full"):
            await controller.acquire(AdmissionRequest(
                tenant_id="tenant-a", run_id="run-b", scope_type="queue",
                scope_id="shared", strategy="queue", wait_timeout_seconds=0.2,
            ))
        with self.assertRaises(BudgetExceeded):
            await first
        self.assertEqual(controller._requests, {})
        self.assertEqual(controller._queues, {})

    async def test_queue_limits_reject_bool_and_inconsistent_bounds(self):
        limiter = SaturatedLimiter()
        for kwargs in (
            {"max_pending_total": True},
            {"max_pending_per_tenant": 0},
            {"max_pending_total": 2, "max_pending_per_tenant": 3},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                TenantFairQuotaAdmissionController(limiter, **kwargs)

if __name__ == "__main__":
    unittest.main()
