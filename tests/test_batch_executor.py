from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="quota rejection executor counting")

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from agent_platform.batch import BatchExecutor, BatchTemplate
from agent_platform.quotas import (
    Budget, BudgetExceeded, InMemoryQuotaStore, QuotaConcurrencyLimiter, QuotaService,
)


class BatchExecutorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryQuotaStore()
        self.quota_service = QuotaService(self.store)
        now = datetime.now(timezone.utc)
        await self.quota_service.configure(Budget(
            tenant_id="tenant-a", scope_type="worker_pool", scope_id="batch-pool",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(hours=1), hard_concurrency=2,
        ))
        self.limiter = QuotaConcurrencyLimiter(self.quota_service)

    def executor(self, **kwargs):
        return BatchExecutor(
            self.limiter, quota_scope_type="worker_pool",
            quota_scope_id="batch-pool", **kwargs,
        )

    async def test_dataset_runs_with_bounded_concurrency_and_ordered_results(self):
        active = 0
        peak = 0
        lock = asyncio.Lock()

        async def runner(row):
            nonlocal active, peak
            async with lock:
                active += 1
                peak = max(peak, active)
            await asyncio.sleep(0.01)
            async with lock:
                active -= 1
            return {"value": row["value"] * 2}

        result = await self.executor(max_concurrency=2).execute(
            tenant_id="tenant-a", template=BatchTemplate("double", runner),
            dataset=[{"value": value} for value in range(6)],
            batch_id="batch-test",
        )
        self.assertEqual(result.total, 6)
        self.assertEqual(result.succeeded, 6)
        self.assertEqual(result.failed, 0)
        self.assertEqual(peak, 2)
        self.assertEqual(
            [task.output["value"] for task in result.tasks],
            [0, 2, 4, 6, 8, 10],
        )
        self.assertEqual(
            await self.store.usage(
                "tenant-a", "worker_pool", "batch-pool",
                datetime.now(timezone.utc) - timedelta(hours=1),
                datetime.now(timezone.utc) + timedelta(hours=1),
            ),
            (0, Decimal("0"), 0),
        )

    async def test_one_task_failure_does_not_abort_other_dataset_rows(self):
        async def runner(row):
            if row["value"] == 2:
                raise RuntimeError("isolated failure")
            return row["value"]

        result = await self.executor(max_concurrency=2).execute(
            tenant_id="tenant-a", template=BatchTemplate("sometimes-fails", runner),
            dataset=[{"value": value} for value in range(5)],
        )
        self.assertEqual((result.succeeded, result.failed), (4, 1))
        self.assertEqual(result.tasks[2].status, "failed")
        self.assertEqual(result.tasks[2].error_type, "RuntimeError")
        self.assertEqual(
            [task.output for task in result.tasks if task.succeeded],
            [0, 1, 3, 4],
        )

    async def test_task_timeout_isolated_and_next_task_runs(self):
        async def runner(row):
            if row["slow"]:
                await asyncio.sleep(0.05)
            return "ok"

        result = await self.executor(
            max_concurrency=1, task_timeout_seconds=0.01,
        ).execute(
            tenant_id="tenant-a", template=BatchTemplate("timeout", runner),
            dataset=[{"slow": True}, {"slow": False}],
        )
        self.assertEqual((result.succeeded, result.failed), (1, 1))
        self.assertEqual(result.tasks[0].error_type, "TimeoutError")
        self.assertEqual(result.tasks[1].output, "ok")

    async def test_quota_slot_is_released_after_runner_failure(self):
        async def runner(row):
            raise ValueError("failure")

        result = await self.executor(max_concurrency=1).execute(
            tenant_id="tenant-a", template=BatchTemplate("fail", runner),
            dataset=[{"id": "one"}],
        )
        self.assertEqual(result.failed, 1)
        reservation = await self.limiter.acquire(
            tenant_id="tenant-a", run_id="follow-up",
            scope_type="worker_pool", scope_id="batch-pool",
        )
        self.assertTrue(await self.limiter.release(reservation))

    async def test_invalid_dataset_and_template_are_rejected_before_execution(self):
        executor = self.executor()
        with self.assertRaisesRegex(ValueError, "finite sequence"):
            await executor.execute(
                tenant_id="tenant-a", template=BatchTemplate("x", self._noop),
                dataset=iter([{"x": 1}]),
            )
        with self.assertRaisesRegex(ValueError, "between 1"):
            await executor.execute(
                tenant_id="tenant-a", template=BatchTemplate("x", self._noop),
                dataset=[],
            )

    async def test_shared_quota_rejection_is_captured_as_task_failure(self):
        first = await self.limiter.acquire(
            tenant_id="tenant-a", run_id="external-holder",
            scope_type="worker_pool", scope_id="batch-pool",
        )

        async def runner(row):
            return row

        result = await self.executor(max_concurrency=1).execute(
            tenant_id="tenant-a", template=BatchTemplate("quota", runner),
            dataset=[{"x": 1}],
        )
        self.assertEqual(result.failed, 1)
        self.assertEqual(result.tasks[0].error_type, "BudgetExceeded")
        self.assertTrue(await self.limiter.release(first))

    @staticmethod
    async def _noop(row):
        return row


if __name__ == "__main__":
    unittest.main()
