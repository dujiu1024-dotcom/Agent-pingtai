"""Production Run worker composition with shared PostgreSQL quota admission.

This factory is deliberately separate from API startup: deployment hosts supply
their trusted registry/runtime executor and worker identity, while quota storage
and concurrency admission remain owned by the production composition root.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter
from agent_platform.quotas.postgres import PostgresQuotaStore
from agent_platform.quotas.service import QuotaService
from agent_platform.runtime.workbench_tasks import RunExecutionWorker


def build_production_run_execution_worker(
    *,
    run_store: Any,
    quota_service: QuotaService,
    executor_resolver: Callable[..., Any],
    lifecycle_resolver: Callable[[str], Any],
    worker_id: str,
    worker_pool_scope_id: str,
    queue_scope_resolver: Callable[[Mapping[str, Any]], str],
    poll_interval: float = 1.0,
    batch_size: int = 10,
    lease_seconds: int = 60,
) -> RunExecutionWorker:
    """Build a Run worker whose queue and worker-pool slots are DB-backed.

    Requiring PostgresQuotaStore prevents a production worker from silently
    accepting process-local quota state. RunExecutionWorker acquires both the
    tenant queue slot and shared worker-pool slot before resolving the executor,
    so quota denial cannot reach model/provider graph construction.
    """
    if not isinstance(quota_service, QuotaService):
        raise TypeError("production Run worker requires QuotaService")
    if not isinstance(quota_service.store, PostgresQuotaStore):
        raise ValueError("production Run worker requires shared PostgresQuotaStore")
    limiter = QuotaConcurrencyLimiter(quota_service)
    return RunExecutionWorker(
        run_store=run_store,
        executor_resolver=executor_resolver,
        lifecycle_resolver=lifecycle_resolver,
        worker_id=worker_id,
        poll_interval=poll_interval,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        concurrency_limiter=limiter,
        worker_pool_scope_id=worker_pool_scope_id,
        queue_scope_resolver=queue_scope_resolver,
    )
