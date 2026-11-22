import asyncio
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from agent_platform.runtime.lifecycle import RunSnapshot, RunStatus, WorkerLeaseLost
from agent_platform.runtime.workbench_tasks import RunExecutionWorker


class _Limiter:
    @asynccontextmanager
    async def slot(self, **_kwargs):
        yield object()


class _LeaseStore:
    def __init__(self, owned):
        self.owned = owned
        self.calls = 0

    async def heartbeat(self, tenant_id, run_id, worker_id, *, lease_seconds, lease_token):
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
                await worker._heartbeat_until_done("tenant-a", "run-a", lease_token=1)

        self.assertEqual(store.calls, 1)

    async def test_interrupted_run_resumes_from_its_checkpoint(self):
        class Store:
            def __init__(self):
                self.output = None

            async def get(self, tenant_id, run_id):
                return RunSnapshot(
                    tenant_id, run_id, RunStatus.SUCCEEDED, 3,
                    checkpoint_ref="checkpoint-42",
                )

            async def save_output(self, tenant_id, run_id, output, *, usage=None, trace_id=None):
                self.output = output

        store = Store()
        worker = self.make_worker(store)
        snapshot = RunSnapshot(
            "tenant-a", "run-a", RunStatus.INTERRUPTED, 2,
            checkpoint_ref="checkpoint-42",
        )

        class Executor:
            def __init__(self):
                self.worker_id = "worker-test"
                self.lifecycle = SimpleNamespace(store=store)
                self.resumed_snapshot = None

            async def execute(self, **_kwargs):
                raise AssertionError("interrupted Runs must resume, not start over")

            async def resume_claimed(self, *, snapshot, input, session_context=None):
                self.resumed_snapshot = snapshot
                return {"answer": "resumed"}

        executor = Executor()
        with patch("agent_platform.runtime.workbench_tasks.LangGraphRunExecutor", Executor):
            outcome = await worker._execute_claimed(
                snapshot, executor, {"input": {"prompt": "continue"}},
            )

        self.assertEqual(outcome, "succeeded")
        self.assertIs(executor.resumed_snapshot, snapshot)
        self.assertEqual(store.output, {"answer": "resumed"})

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
                    type("Snapshot", (), {"tenant_id": "tenant-a", "run_id": "run-a", "lease_token": 4})(),
                    execute(None, None, {}),
                )

        self.assertTrue(cancelled.is_set())
        self.assertEqual(store.calls, 1)


if __name__ == "__main__":
    unittest.main()
