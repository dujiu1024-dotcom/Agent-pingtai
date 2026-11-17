"""Fail-closed assembly of the production Registry-backed Run execution path.

The graph factory and runtime provider remain trusted deployment code. This
module wires the repository-owned task service, durable Run store, executor
resolver and quota-enforced worker without importing arbitrary module paths or
creating an insecure default graph.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agent_platform.runtime.workbench_tasks import (
    RunExecutionWorker,
    create_registry_backed_task_service_resolver,
)


@dataclass(frozen=True)
class ProductionRunExecution:
    """The complete set of Run execution ports installed into the production API."""

    run_store: Any
    task_service_resolver: Callable[..., Any]
    task_authorizer: Callable[..., Any]
    run_executor_resolver: Callable[..., Any]
    task_worker: RunExecutionWorker


def build_production_run_execution(
    *,
    run_store: Any,
    lifecycle: Any,
    registry_resolver: Callable[..., Any],
    snapshot_resolver: Callable[..., Any],
    executor_resolver: Callable[..., Any],
    task_authorizer: Callable[..., Any],
    concurrency_limiter: Any,
    worker_pool_scope_id: str,
    queue_scope_resolver: Callable[..., str],
    worker_id: str,
    poll_interval: float = 1.0,
    batch_size: int = 10,
    lease_seconds: int = 60,
) -> ProductionRunExecution:
    """Build task API + durable execution worker from trusted, explicit ports.

    The lifecycle, API task service and worker must share the exact same
    PostgreSQL RunStateStore instance. The executor resolver is supplied by
    trusted deployment code (normally RegistryLangGraphExecutorResolver), so
    graph construction, Vault secret resolution, reviewed Skills and trusted
    Tool handlers cannot be sourced from Agent-authored import strings.
    """
    if run_store is None or getattr(lifecycle, "store", None) is not run_store:
        raise ValueError("production Run lifecycle and worker must share one durable RunStateStore")
    for name, callback in (
        ("registry_resolver", registry_resolver),
        ("snapshot_resolver", snapshot_resolver),
        ("executor_resolver", executor_resolver),
        ("task_authorizer", task_authorizer),
        ("queue_scope_resolver", queue_scope_resolver),
    ):
        if not callable(callback):
            raise ValueError(f"{name} must be a trusted callable")
    if not callable(getattr(concurrency_limiter, "slot", None)):
        raise ValueError("production Run execution requires a shared quota concurrency limiter")
    if not isinstance(worker_pool_scope_id, str) or not worker_pool_scope_id.strip():
        raise ValueError("worker_pool_scope_id is required")

    async def lifecycle_resolver(_tenant_id: str) -> Any:
        return lifecycle

    task_service_resolver = create_registry_backed_task_service_resolver(
        registry_resolver=registry_resolver,
        snapshot_resolver=snapshot_resolver,
        run_store=run_store,
        lifecycle_resolver=lifecycle_resolver,
    )
    worker = RunExecutionWorker(
        run_store=run_store,
        executor_resolver=executor_resolver,
        lifecycle_resolver=lifecycle_resolver,
        worker_id=worker_id,
        poll_interval=poll_interval,
        batch_size=batch_size,
        lease_seconds=lease_seconds,
        concurrency_limiter=concurrency_limiter,
        worker_pool_scope_id=worker_pool_scope_id,
        queue_scope_resolver=queue_scope_resolver,
    )
    return ProductionRunExecution(
        run_store=run_store,
        task_service_resolver=task_service_resolver,
        task_authorizer=task_authorizer,
        run_executor_resolver=executor_resolver,
        task_worker=worker,
    )


def load_trusted_run_execution_builder(*, required: bool) -> Callable[..., ProductionRunExecution] | None:
    """Load the one deployment-installed, reviewed runtime integration entry point.

    The entry-point group is controlled by installed Python distributions; no
    Agent definition or environment variable can supply an import path.
    """
    from importlib.metadata import entry_points

    candidates = tuple(
        entry for entry in entry_points(group="agent_platform.production_run_execution")
        if entry.name == "builder"
    )
    if len(candidates) > 1:
        raise RuntimeError(
            "multiple production Run execution builders are installed; exactly one is allowed"
        )
    if not candidates:
        if required:
            raise RuntimeError(
                "production Run execution is required but no trusted "
                "'builder' entry point is installed in group "
                "'agent_platform.production_run_execution'"
            )
        return None
    builder = candidates[0].load()
    if not callable(builder):
        raise TypeError("trusted production Run execution entry point must load a callable")
    return builder
