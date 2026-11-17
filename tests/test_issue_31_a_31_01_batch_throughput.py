from __future__ import annotations

import asyncio
import math
import time
import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.batch import BatchExecutor, BatchTemplate
from agent_platform.quotas import (
    Budget,
    InMemoryQuotaStore,
    QuotaConcurrencyLimiter,
    QuotaService,
)


class BatchThroughputAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        now = datetime.now(timezone.utc)
        await service.configure(Budget(
            tenant_id="tenant-a",
            scope_type="worker_pool",
            scope_id="batch-throughput",
            period_start=now - timedelta(minutes=1),
            period_end=now + timedelta(hours=1),
            hard_concurrency=8,
        ))
        self.limiter = QuotaConcurrencyLimiter(service)

    async def test_100_tasks_complete_within_twice_serial_wave_budget(self):
        concurrency = 8
        task_delay_seconds = 0.05
        executor = BatchExecutor(
            self.limiter,
            max_concurrency=concurrency,
            quota_scope_type="worker_pool",
            quota_scope_id="batch-throughput",
            task_timeout_seconds=2.0,
            max_dataset_size=100,
        )
        active = 0
        peak_active = 0
        lock = asyncio.Lock()

        async def runner(row):
            nonlocal active, peak_active
            async with lock:
                active += 1
                peak_active = max(peak_active, active)
            try:
                await asyncio.sleep(task_delay_seconds)
                return row["index"]
            finally:
                async with lock:
                    active -= 1

        template = BatchTemplate("throughput-acceptance", runner)

        # Use the slowest of three isolated samples as a conservative single-task baseline.
        single_task_samples = []
        for sample in range(3):
            started = time.perf_counter()
            single = await executor.execute(
                tenant_id="tenant-a",
                template=template,
                dataset=[{"index": sample}],
                batch_id=f"single-{sample}",
            )
            single_task_samples.append(time.perf_counter() - started)
            self.assertEqual((single.total, single.succeeded, single.failed), (1, 1, 0))

        single_task_seconds = max(single_task_samples)
        dataset = [{"index": index} for index in range(100)]
        started = time.perf_counter()
        result = await executor.execute(
            tenant_id="tenant-a",
            template=template,
            dataset=dataset,
            batch_id="batch-100",
        )
        batch_seconds = time.perf_counter() - started

        waves = math.ceil(100 / concurrency)
        allowed_seconds = single_task_seconds * waves * 2

        self.assertEqual((result.total, result.succeeded, result.failed), (100, 100, 0))
        self.assertEqual([task.output for task in result.tasks], list(range(100)))
        self.assertEqual(peak_active, concurrency)
        self.assertLessEqual(
            batch_seconds,
            allowed_seconds,
            msg=(
                f"100 tasks took {batch_seconds:.3f}s; limit is {allowed_seconds:.3f}s "
                f"(single task {single_task_seconds:.3f}s, concurrency {concurrency}, "
                f"waves {waves})"
            ),
        )


if __name__ == "__main__":
    unittest.main()
