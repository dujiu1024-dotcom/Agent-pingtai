from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="build_production_run_execution_worker missing")

import ast
import unittest
from pathlib import Path

from agent_platform.quotas.postgres import PostgresQuotaStore
from agent_platform.quotas.production_worker import build_production_run_execution_worker
from agent_platform.quotas.service import InMemoryQuotaStore, QuotaService
from agent_platform.runtime.workbench_tasks import RunExecutionWorker


ROOT = Path(__file__).resolve().parents[1]


class ProductionRunWorkerQuotaFactoryTests(unittest.TestCase):
    def test_factory_pins_worker_to_shared_postgres_quota_service(self):
        async def connection_factory():
            raise AssertionError("factory construction must not open a database connection")

        quota_service = QuotaService(PostgresQuotaStore(connection_factory))
        run_store = object()
        executor_resolver = lambda *_args: None
        lifecycle_resolver = lambda _tenant: None
        queue_scope_resolver = lambda record: str(record["queue_id"])

        worker = build_production_run_execution_worker(
            run_store=run_store,
            quota_service=quota_service,
            executor_resolver=executor_resolver,
            lifecycle_resolver=lifecycle_resolver,
            worker_id="production-worker-a",
            worker_pool_scope_id="default-pool",
            queue_scope_resolver=queue_scope_resolver,
            poll_interval=2,
            batch_size=7,
            lease_seconds=45,
        )

        self.assertIsInstance(worker, RunExecutionWorker)
        self.assertIs(worker.run_store, run_store)
        self.assertIs(worker.executor_resolver, executor_resolver)
        self.assertIs(worker.lifecycle_resolver, lifecycle_resolver)
        self.assertIs(worker.concurrency_limiter.quota_service, quota_service)
        self.assertEqual(worker.worker_pool_scope_id, "default-pool")
        self.assertIs(worker.queue_scope_resolver, queue_scope_resolver)
        self.assertEqual(worker.poll_interval, 2)
        self.assertEqual(worker.batch_size, 7)
        self.assertEqual(worker.lease_seconds, 45)

    def test_factory_rejects_process_local_quota_store(self):
        quota_service = QuotaService(InMemoryQuotaStore())
        with self.assertRaisesRegex(ValueError, "PostgresQuotaStore"):
            build_production_run_execution_worker(
                run_store=object(),
                quota_service=quota_service,
                executor_resolver=lambda *_args: None,
                lifecycle_resolver=lambda _tenant: None,
                worker_id="production-worker-a",
                worker_pool_scope_id="default-pool",
                queue_scope_resolver=lambda _record: "default",
            )

    def test_production_composition_exposes_factory_pinned_to_shared_services(self):
        source = (ROOT / "agent_platform" / "production.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertIn("build_production_run_execution_worker", source)
        self.assertIn("app.state.run_execution_worker_factory", source)
        self.assertIn("run_store=run_store", source)
        self.assertIn("quota_service=quota_service", source)
        self.assertIn("lifecycle_resolver=resolve_run_lifecycle", source)
        self.assertIn("partial(", source)
        self.assertTrue(any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "PostgresQuotaStore"
            for node in ast.walk(tree)
        ))


if __name__ == "__main__":
    unittest.main()
