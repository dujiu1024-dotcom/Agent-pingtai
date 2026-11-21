from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace

from agent_platform.runtime.workbench_tasks import RunExecutionWorker
from agent_platform.runtime.production_composition import (
    ProductionRunExecution,
    build_production_run_execution,
)


class FakeRunStore:
    async def claim_workbench_tasks(self, *_args, **_kwargs):
        return []


class ClaimedTaskDisappearsStore(FakeRunStore):
    def __init__(self):
        self.released = []
        self.claim_args = None

    async def claim_workbench_tasks(self, worker_id, *, limit, lease_seconds):
        self.claim_args = (worker_id, limit, lease_seconds)
        return [SimpleNamespace(tenant_id="tenant-a", run_id="run-1")]

    async def get_task_record(self, tenant_id, run_id):
        return None

    async def release_lease(self, tenant_id, run_id, worker_id):
        self.released.append((tenant_id, run_id, worker_id))


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


class RunWorkerDispatchContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_task_record_releases_claimed_lease_without_resolving_executor(self):
        store = ClaimedTaskDisappearsStore()
        executor_calls = []
        worker = RunExecutionWorker(
            run_store=store,
            executor_resolver=lambda record, worker_id: executor_calls.append((record, worker_id)),
            lifecycle_resolver=lambda _tenant_id: FakeLifecycle(store),
            worker_id="worker-test-2",
            poll_interval=0.01,
            concurrency_limiter=FakeLimiter(),
            worker_pool_scope_id="production-workers",
            queue_scope_resolver=lambda _record: "default",
        )

        counts = await worker.run_once()

        self.assertEqual(counts, {
            "claimed": 1, "succeeded": 0, "failed": 0,
            "cancelled": 0, "skipped": 1,
        })
        self.assertEqual(store.claim_args, ("worker-test-2", 10, 60))
        self.assertEqual(store.released, [("tenant-a", "run-1", "worker-test-2")])
        self.assertEqual(executor_calls, [])


class TrustedBuilderDiscoveryTests(unittest.TestCase):
    def test_optional_missing_builder_keeps_task_execution_disabled(self):
        from unittest.mock import patch

        with patch("importlib.metadata.entry_points", return_value=[]):
            from agent_platform.runtime.production_composition import (
                load_trusted_run_execution_builder,
            )
            self.assertIsNone(load_trusted_run_execution_builder(required=False))

    def test_required_missing_builder_fails_closed(self):
        from unittest.mock import patch

        with patch("importlib.metadata.entry_points", return_value=[]):
            from agent_platform.runtime.production_composition import (
                load_trusted_run_execution_builder,
            )
            with self.assertRaisesRegex(RuntimeError, "no trusted"):
                load_trusted_run_execution_builder(required=True)

    def test_multiple_or_non_callable_builders_are_rejected(self):
        from unittest.mock import patch

        from agent_platform.runtime.production_composition import (
            load_trusted_run_execution_builder,
        )
        entry = SimpleNamespace(name="builder", load=lambda: object())
        with patch("importlib.metadata.entry_points", return_value=[entry, entry]):
            with self.assertRaisesRegex(RuntimeError, "multiple"):
                load_trusted_run_execution_builder(required=True)
        with patch("importlib.metadata.entry_points", return_value=[entry]):
            with self.assertRaisesRegex(TypeError, "callable"):
                load_trusted_run_execution_builder(required=True)


class ProductionEntrypointWiringTests(unittest.TestCase):
    def test_api_role_can_disable_in_process_run_worker(self):
        import os
        import asyncio
        from unittest.mock import patch

        from fastapi import FastAPI
        from agent_platform.production_config import ProductionSettings

        settings = ProductionSettings(
            postgres_dsn="postgresql://platform@db/platform",
            redis_url="redis://redis:6379/0",
            allowed_origins=("https://platform.example.com",),
        )
        with patch.dict(os.environ, {
            "AGENT_PLATFORM_DSN": "postgresql://platform@db/platform",
            "REDIS_URL": "redis://redis:6379/0",
            "ALLOWED_ORIGINS": "https://platform.example.com",
            "AGENT_PLATFORM_RUN_WORKER_ENABLED": "false",
        }), patch(
            "agent_platform.production.create_platform_app", return_value=FastAPI()
        ):
            from agent_platform.production import create_production_app
            app = create_production_app(
                settings=settings,
                connection_factory=lambda: None,
                redis_client=SimpleNamespace(ping=lambda: True),
            )
        start_worker = next(
            callback for callback in app.router.on_startup
            if getattr(callback, "__name__", "") == "start_run_worker"
        )
        asyncio.run(start_worker())

    def test_production_root_installs_builder_runtime_into_api_and_worker_slots(self):
        import importlib
        import os
        from unittest.mock import patch

        from fastapi import FastAPI
        from agent_platform.production_config import ProductionSettings

        fake_redis = SimpleNamespace(ping=lambda: True)
        environment = {
            "AGENT_PLATFORM_DSN": "postgresql://platform@db/platform",
            "REDIS_URL": "redis://redis:6379/0",
            "ALLOWED_ORIGINS": "https://platform.example.com",
            "AGENT_PLATFORM_REQUIRE_RUN_EXECUTION": "false",
        }
        from redis.asyncio import Redis

        with patch.dict(os.environ, environment), patch.object(
            Redis, "from_url", return_value=fake_redis
        ):
            production = importlib.import_module("agent_platform.production")

        settings = ProductionSettings(
            postgres_dsn="postgresql://platform@db/platform",
            redis_url="redis://redis:6379/0",
            allowed_origins=("https://platform.example.com",),
        )
        builder_inputs = {}
        runtime_holder = {}

        def trusted_builder(**values):
            builder_inputs.update(values)
            runtime = build_production_run_execution(
                run_store=values["run_store"],
                lifecycle=values["lifecycle"],
                registry_resolver=lambda principal: SimpleNamespace(
                    repository=SimpleNamespace(tenant_id=principal.tenant_id),
                ),
                snapshot_resolver=lambda _principal: object(),
                executor_resolver=lambda _record, _worker_id: None,
                task_authorizer=lambda _principal, _action, _task_id=None: True,
                concurrency_limiter=FakeLimiter(),
                worker_pool_scope_id="production-workers",
                queue_scope_resolver=lambda _record: "default",
                worker_id="production-entrypoint-test",
                poll_interval=0.01,
            )
            runtime_holder["runtime"] = runtime
            return runtime

        with patch.object(
            production, "create_platform_app", return_value=FastAPI()
        ) as create_platform_app:
            app = production.create_production_app(
                settings=settings,
                connection_factory=lambda: None,
                redis_client=fake_redis,
                run_execution_builder=trusted_builder,
            )

        runtime = runtime_holder["runtime"]
        self.assertIs(app.state.run_execution, runtime)
        self.assertIs(app.state.run_state_store, runtime.run_store)
        self.assertIs(builder_inputs["run_store"], app.state.run_state_store)
        self.assertIs(builder_inputs["lifecycle"].store, app.state.run_state_store)
        installed = create_platform_app.call_args.kwargs
        self.assertIs(installed["task_worker"], runtime.task_worker)
        self.assertIs(installed["task_service_resolver"], runtime.task_service_resolver)
        self.assertIs(installed["task_authorizer"], runtime.task_authorizer)
        self.assertIs(installed["run_executor_resolver"], runtime.run_executor_resolver)



if __name__ == "__main__":
    unittest.main()
