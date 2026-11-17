from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from agent_platform.runtime.production_composition import (
    ProductionRunExecution,
    build_production_run_execution,
)


class FakeRunStore:
    async def claim_workbench_tasks(self, *_args, **_kwargs):
        return []


class FakeLifecycle:
    def __init__(self, store):
        self.store = store


class FakeLimiter:
    @asynccontextmanager
    async def slot(self, **_kwargs):
        yield


class ProductionRunCompositionTests(unittest.IsolatedAsyncioTestCase):
    def build(self, **overrides):
        run_store = overrides.pop("run_store", FakeRunStore())
        lifecycle = overrides.pop("lifecycle", FakeLifecycle(run_store))
        values = {
            "run_store": run_store,
            "lifecycle": lifecycle,
            "registry_resolver": lambda principal: SimpleNamespace(
                repository=SimpleNamespace(tenant_id=principal.tenant_id),
            ),
            "snapshot_resolver": lambda _principal: object(),
            "executor_resolver": lambda _record, _worker_id: None,
            "task_authorizer": lambda _principal, _action, _task_id=None: True,
            "concurrency_limiter": FakeLimiter(),
            "worker_pool_scope_id": "production-workers",
            "queue_scope_resolver": lambda _record: "default",
            "worker_id": "worker-test-1",
            "poll_interval": 0.01,
        }
        values.update(overrides)
        return run_store, build_production_run_execution(**values)

    async def test_composes_tenant_bound_task_service_and_shared_worker(self):
        run_store, runtime = self.build()
        self.assertIsInstance(runtime, ProductionRunExecution)
        self.assertIs(runtime.run_store, run_store)
        self.assertIs(runtime.task_worker.run_store, run_store)
        self.assertIs(runtime.task_worker.executor_resolver, runtime.run_executor_resolver)
        self.assertIsNotNone(runtime.task_service_resolver)
        principal = SimpleNamespace(tenant_id="tenant-a")
        service = await runtime.task_service_resolver(principal)
        self.assertEqual(service.tenant_id, "tenant-a")
        self.assertIs(service.run_store, run_store)

    async def test_worker_lifecycle_starts_and_stops_cleanly(self):
        _run_store, runtime = self.build()
        await runtime.task_worker.start()
        self.assertIsNotNone(runtime.task_worker._task)
        await runtime.task_worker.stop()
        self.assertIsNone(runtime.task_worker._task)

    def test_rejects_lifecycle_using_a_different_run_store(self):
        with self.assertRaisesRegex(ValueError, "same durable RunStateStore"):
            self.build(lifecycle=FakeLifecycle(FakeRunStore()))

    def test_rejects_missing_shared_quota_limiter(self):
        with self.assertRaisesRegex(ValueError, "shared quota concurrency limiter"):
            self.build(concurrency_limiter=object())

    def test_rejects_untrusted_or_missing_composition_ports(self):
        with self.assertRaisesRegex(ValueError, "executor_resolver"):
            self.build(executor_resolver=None)
        with self.assertRaisesRegex(ValueError, "task_authorizer"):
            self.build(task_authorizer=None)
        with self.assertRaisesRegex(ValueError, "queue_scope_resolver"):
            self.build(queue_scope_resolver=None)


if __name__ == "__main__":
    unittest.main()
