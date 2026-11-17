"""Isolated, quota-backed asynchronous batch execution.

The executor owns per-batch fan-out and result collection only. Shared concurrency
admission remains the responsibility of :mod:`agent_platform.quotas.concurrency`.
"""
from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import Any
from uuid import uuid4

from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter

BatchRunner = Callable[[Mapping[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class BatchTemplate:
    """Reusable task template whose runner receives one dataset row."""

    template_id: str
    runner: BatchRunner

    def __post_init__(self) -> None:
        if not isinstance(self.template_id, str) or not self.template_id.strip():
            raise ValueError("template_id is required")
        if not callable(self.runner):
            raise ValueError("template runner must be callable")


@dataclass(frozen=True)
class BatchTaskResult:
    """One isolated dataset execution result."""

    task_id: str
    index: int
    status: str
    output: Any
    error_type: str | None
    error_message: str | None
    started_at: datetime
    finished_at: datetime
    duration_ms: int

    @property
    def succeeded(self) -> bool:
        return self.status == "succeeded"


@dataclass(frozen=True)
class BatchExecutionResult:
    """Immutable batch envelope preserving input order."""

    batch_id: str
    tenant_id: str
    template_id: str
    total: int
    succeeded: int
    failed: int
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    tasks: tuple[BatchTaskResult, ...]

    @property
    def success_rate(self) -> float:
        return self.succeeded / self.total if self.total else 1.0


class BatchExecutor:
    """Run a dataset with local fan-out and shared quota-backed concurrency slots."""

    def __init__(
        self,
        concurrency_limiter: QuotaConcurrencyLimiter,
        *,
        max_concurrency: int = 8,
        quota_scope_type: str = "worker_pool",
        quota_scope_id: str = "batch-execution",
        task_timeout_seconds: float = 300.0,
        max_dataset_size: int = 10_000,
    ) -> None:
        if not isinstance(concurrency_limiter, QuotaConcurrencyLimiter):
            raise TypeError("concurrency_limiter must be a QuotaConcurrencyLimiter")
        if (
            isinstance(max_concurrency, bool)
            or not isinstance(max_concurrency, int)
            or not 1 <= max_concurrency <= 1000
        ):
            raise ValueError("max_concurrency must be between 1 and 1000")
        if quota_scope_type not in {"queue", "worker_pool"}:
            raise ValueError("quota_scope_type must be queue or worker_pool")
        if not isinstance(quota_scope_id, str) or not quota_scope_id.strip():
            raise ValueError("quota_scope_id is required")
        if (
            isinstance(task_timeout_seconds, bool)
            or not isinstance(task_timeout_seconds, (int, float))
            or task_timeout_seconds <= 0
        ):
            raise ValueError("task_timeout_seconds must be positive")
        if (
            isinstance(max_dataset_size, bool)
            or not isinstance(max_dataset_size, int)
            or not 1 <= max_dataset_size <= 100_000
        ):
            raise ValueError("max_dataset_size must be between 1 and 100000")
        self.concurrency_limiter = concurrency_limiter
        self.max_concurrency = max_concurrency
        self.quota_scope_type = quota_scope_type
        self.quota_scope_id = quota_scope_id
        self.task_timeout_seconds = float(task_timeout_seconds)
        self.max_dataset_size = max_dataset_size

    @staticmethod
    def _validate_dataset(dataset: Sequence[Mapping[str, Any]], limit: int) -> tuple[Mapping[str, Any], ...]:
        if isinstance(dataset, (str, bytes)) or not isinstance(dataset, Sequence):
            raise ValueError("dataset must be a finite sequence of input mappings")
        if not 1 <= len(dataset) <= limit:
            raise ValueError(f"dataset must contain between 1 and {limit} rows")
        rows: list[Mapping[str, Any]] = []
        for index, row in enumerate(dataset):
            if not isinstance(row, Mapping):
                raise ValueError(f"dataset row {index} must be a mapping")
            rows.append(MappingProxyType(dict(row)))
        return tuple(rows)

    async def execute(
        self,
        *,
        tenant_id: str,
        template: BatchTemplate,
        dataset: Sequence[Mapping[str, Any]],
        batch_id: str | None = None,
    ) -> BatchExecutionResult:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(template, BatchTemplate):
            raise TypeError("template must be a BatchTemplate")
        rows = self._validate_dataset(dataset, self.max_dataset_size)
        resolved_batch_id = batch_id or str(uuid4())
        if not isinstance(resolved_batch_id, str) or not resolved_batch_id.strip():
            raise ValueError("batch_id must be a non-empty string")
        semaphore = asyncio.Semaphore(self.max_concurrency)
        batch_started = datetime.now(timezone.utc)
        batch_clock = time.monotonic()

        async def execute_one(index: int, row: Mapping[str, Any]) -> BatchTaskResult:
            task_id = f"{resolved_batch_id}:{index}"
            started_at = datetime.now(timezone.utc)
            started_clock = time.monotonic()
            try:
                async with semaphore:
                    async with self.concurrency_limiter.slot(
                        tenant_id=tenant_id,
                        run_id=task_id,
                        scope_type=self.quota_scope_type,
                        scope_id=self.quota_scope_id,
                    ):
                        async with asyncio.timeout(self.task_timeout_seconds):
                            output = template.runner(dict(row))
                            if not inspect.isawaitable(output):
                                raise TypeError("template runner must return an awaitable")
                            output = await output
                return BatchTaskResult(
                    task_id=task_id, index=index, status="succeeded",
                    output=output, error_type=None, error_message=None,
                    started_at=started_at, finished_at=datetime.now(timezone.utc),
                    duration_ms=max(0, int((time.monotonic() - started_clock) * 1000)),
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                message = str(exc).replace("\n", " ")[:500] or None
                return BatchTaskResult(
                    task_id=task_id, index=index, status="failed",
                    output=None, error_type=type(exc).__name__,
                    error_message=message,
                    started_at=started_at, finished_at=datetime.now(timezone.utc),
                    duration_ms=max(0, int((time.monotonic() - started_clock) * 1000)),
                )

        tasks = await asyncio.gather(
            *(execute_one(index, row) for index, row in enumerate(rows))
        )
        finished_at = datetime.now(timezone.utc)
        succeeded = sum(task.succeeded for task in tasks)
        return BatchExecutionResult(
            batch_id=resolved_batch_id, tenant_id=tenant_id,
            template_id=template.template_id, total=len(tasks),
            succeeded=succeeded, failed=len(tasks) - succeeded,
            started_at=batch_started, finished_at=finished_at,
            duration_ms=max(0, int((time.monotonic() - batch_clock) * 1000)),
            tasks=tuple(tasks),
        )
