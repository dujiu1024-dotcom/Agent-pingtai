import asyncio
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from agent_platform.runtime.lifecycle import WorkerLeaseLost
from agent_platform.runtime.workbench_tasks import RunExecutionWorker


class _Limiter:
    @asynccontextmanager
    async def slot(self, **_kwargs):
        yield object()


class _LeaseStore:
    def __init__(self, owned):
        self.owned = owned
        self.calls = 0

    async def heartbeat(self, tenant_id, run_id, worker_id, *, lease_seconds):
        self.calls += 1
        return self.owned


class WorkbenchWorkerReliabilityTests(unittest.IsolatedAsyncioTestCase):
    def make_worker(self, store):
        return RunExecutionWorker(
            run_store=store,
            executor_resolver=lambda *_args: None,
            lifecycle_resolver=lambda _tenant: None,
            worker_id="worker-test",
            lease_seconds=5,
            concurrency_limiter=_Limiter(),
            worker_pool_scope_id="tests",
            queue_scope_resolver=lambda _record: "default",
        )

    async def test_heartbeat_raises_when_lease_is_lost(self):
        store = _LeaseStore(owned=False)
        worker = self.make_worker(store)

        async def immediate_sleep(_seconds):
            return None

        with patch("agent_platform.runtime.workbench_tasks.asyncio.sleep", immediate_sleep):
            with self.assertRaises(WorkerLeaseLost):
                await worker._heartbeat_until_done("tenant-a", "run-a")

        self.assertEqual(store.calls, 1)

    async def test_execution_is_cancelled_when_heartbeat_loses_lease(self):
        store = _LeaseStore(owned=False)
        worker = self.make_worker(store)
        cancelled = asyncio.Event()

        async def execute(_snapshot, _executor, _record):
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                raise

        worker._execute_claimed = execute

        original_sleep = asyncio.sleep

        async def immediate_sleep(_seconds):
            await original_sleep(0)

        with patch("agent_platform.runtime.workbench_tasks.asyncio.sleep", immediate_sleep):
            with self.assertRaises(WorkerLeaseLost):
                await worker._with_heartbeat(
                    type("Snapshot", (), {"tenant_id": "tenant-a", "run_id": "run-a"})(),
                    execute(None, None, {}),
                )

        self.assertTrue(cancelled.is_set())
        self.assertEqual(store.calls, 1)


if __name__ == "__main__":
    unittest.main()
