from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.batch import BatchExecutor, BatchTemplate
from agent_platform.quotas import (
    Budget,
    InMemoryQuotaStore,
    QuotaConcurrencyLimiter,
    QuotaService,
)


class BatchFailureIsolationAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        now = datetime.now(timezone.utc)
        await service.configure(Budget(
            tenant_id="tenant-a",
            scope_type="worker_pool",
            scope_id="batch-pool",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(hours=1),
            hard_concurrency=2,
        ))
        self.limiter = QuotaConcurrencyLimiter(service)

    def executor(self, *, timeout=1.0):
        return BatchExecutor(
            self.limiter,
            max_concurrency=2,
            quota_scope_type="worker_pool",
            quota_scope_id="batch-pool",
            task_timeout_seconds=timeout,
        )

    async def test_memory_error_isolated_and_remaining_rows_complete(self):
        started = []

        async def runner(row):
            started.append(row["id"])
            if row["id"] == "oom":
                raise MemoryError("simulated allocation failure")
            await asyncio.sleep(0)
            return row["id"]

        result = await self.executor().execute(
            tenant_id="tenant-a",
            template=BatchTemplate("memory-isolation", runner),
            dataset=[{"id": value} for value in ("oom", "ok-1", "ok-2", "ok-3")],
            batch_id="memory-isolation",
        )

        self.assertEqual(set(started), {"oom", "ok-1", "ok-2", "ok-3"})
        self.assertEqual((result.total, result.succeeded, result.failed), (4, 3, 1))
        self.assertEqual(result.tasks[0].status, "failed")
        self.assertEqual(result.tasks[0].error_type, "MemoryError")
        self.assertEqual(
            [task.output for task in result.tasks[1:]],
            ["ok-1", "ok-2", "ok-3"],
        )

    async def test_timeout_isolated_and_remaining_rows_complete(self):
        started = []
        cancelled = asyncio.Event()

        async def runner(row):
            started.append(row["id"])
            if row["id"] == "slow":
                try:
                    await asyncio.sleep(0.2)
                finally:
                    cancelled.set()
            return row["id"]

        result = await self.executor(timeout=0.02).execute(
            tenant_id="tenant-a",
            template=BatchTemplate("timeout-isolation", runner),
            dataset=[{"id": value} for value in ("slow", "ok-1", "ok-2", "ok-3")],
            batch_id="timeout-isolation",
        )

        self.assertTrue(cancelled.is_set(), "timed-out coroutine must be cancelled")
        self.assertEqual(set(started), {"slow", "ok-1", "ok-2", "ok-3"})
        self.assertEqual((result.total, result.succeeded, result.failed), (4, 3, 1))
        self.assertEqual(result.tasks[0].status, "failed")
        self.assertEqual(result.tasks[0].error_type, "TimeoutError")
        self.assertEqual(
            [task.output for task in result.tasks[1:]],
            ["ok-1", "ok-2", "ok-3"],
        )


if __name__ == "__main__":
    unittest.main()
