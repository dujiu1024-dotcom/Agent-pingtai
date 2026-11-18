"""持久化批次执行服务。

数据库（platform_batch_jobs）是批次作业的唯一事实来源；进程内只登记正在
执行的后台任务。平台重启时把遗留的 queued/running 作业标记为 interrupted，
避免出现永远处于 running 的假状态。
"""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from agent_platform.batch.executor import BatchExecutionResult, BatchExecutor, BatchTemplate
from agent_platform.quotas.concurrency import QuotaConcurrencyLimiter

TEMPLATE_AGENT_TASK = "agent-task"
TERMINAL_TASK_STATUSES = frozenset(
    {"succeeded", "failed", "cancelled", "timed_out", "interrupted"}
)
ACTIVE_BATCH_STATUSES = frozenset({"queued", "running"})


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError:
        return value
    return Jsonb(value)


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value


def _task_result_payload(task: Any, index: int) -> dict[str, Any]:
    return {
        "task_id": task.task_id,
        "index": index,
        "status": task.status,
        "output": task.output,
        "error_type": task.error_type,
        "error_message": task.error_message,
        "started_at": _iso(task.started_at),
        "finished_at": _iso(task.finished_at),
        "duration_ms": task.duration_ms,
    }


def _job_view(row: Mapping[str, Any], *, include_dataset: bool = True) -> dict[str, Any]:
    view: dict[str, Any] = {
        "batch_id": str(row["batch_id"]),
        "tenant_id": row["tenant_id"],
        "actor_id": row["actor_id"],
        "template_id": row["template_id"],
        "status": row["status"],
        "total": row["total"],
        "succeeded": row["succeeded"],
        "failed": row["failed"],
        "max_concurrency": row["max_concurrency"],
        "results": list(row.get("results") or []),
        "error": row.get("error"),
        "created_at": _iso(row.get("created_at")),
        "updated_at": _iso(row.get("updated_at")),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
    }
    if include_dataset:
        view["dataset"] = list(row.get("dataset") or [])
    return view


class PostgresBatchRepository:
    """租户范围内的批次作业读写；tenant_id 由服务绑定，绝不来自请求体。"""

    def __init__(self, *, tenant_id: str, connection_factory: Any) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        self.tenant_id = tenant_id
        self._connect = connection_factory

    async def create(
        self, *, batch_id: str, actor_id: str, template_id: str,
        max_concurrency: int, dataset: Sequence[Mapping[str, Any]],
        idempotency_key: str | None, request_digest: str,
    ) -> Mapping[str, Any]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_batch_jobs
                (batch_id,tenant_id,actor_id,template_id,status,total,max_concurrency,
                 dataset,idempotency_key,request_digest)
                VALUES (%s,%s,%s,%s,'queued',%s,%s,%s,%s,%s)
                RETURNING *""",
                (
                    batch_id, self.tenant_id, actor_id, template_id, len(dataset),
                    max_concurrency, _jsonb([dict(row) for row in dataset]),
                    idempotency_key, request_digest,
                ),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RuntimeError("batch job could not be persisted")
        return row

    async def get(self, batch_id: str) -> Mapping[str, Any] | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_batch_jobs WHERE tenant_id=%s AND batch_id=%s""",
                (self.tenant_id, batch_id),
            )
            return await cursor.fetchone()

    async def list_jobs(self, *, actor_id: str, limit: int) -> list[Mapping[str, Any]]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT batch_id,tenant_id,actor_id,template_id,status,total,succeeded,
                          failed,max_concurrency,error,created_at,updated_at,started_at,finished_at
                FROM platform_batch_jobs WHERE tenant_id=%s AND actor_id=%s
                ORDER BY created_at DESC LIMIT %s""",
                (self.tenant_id, actor_id, limit),
            )
            return list(await cursor.fetchall())

    async def find_by_idempotency(self, key: str) -> Mapping[str, Any] | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_batch_jobs
                WHERE tenant_id=%s AND idempotency_key=%s LIMIT 1""",
                (self.tenant_id, key),
            )
            return await cursor.fetchone()

    async def retry_key_owned_elsewhere(self, key: str, batch_id: str) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT 1 FROM platform_batch_jobs
                WHERE tenant_id=%s AND batch_id<>%s
                  AND (idempotency_key=%s OR retry_keys ? %s) LIMIT 1""",
                (self.tenant_id, batch_id, key, key),
            )
            return await cursor.fetchone() is not None

    async def mark_running(self, batch_id: str) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_batch_jobs
                SET status='running', started_at=now(), updated_at=now()
                WHERE tenant_id=%s AND batch_id=%s AND status='queued'""",
                (self.tenant_id, batch_id),
            )
            return (cursor.rowcount or 0) > 0

    async def finish(
        self, batch_id: str, *, status: str, succeeded: int, failed: int,
        results: Sequence[Mapping[str, Any]],
    ) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """UPDATE platform_batch_jobs
                SET status=%s, succeeded=%s, failed=%s, results=%s, error=NULL,
                    updated_at=now(), finished_at=now()
                WHERE tenant_id=%s AND batch_id=%s AND status='running'""",
                (status, succeeded, failed, _jsonb(list(results)), self.tenant_id, batch_id),
            )

    async def fail(self, batch_id: str, message: str) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """UPDATE platform_batch_jobs
                SET status='failed', error=%s, updated_at=now(), finished_at=now()
                WHERE tenant_id=%s AND batch_id=%s AND status IN ('queued','running')""",
                (message[:1000], self.tenant_id, batch_id),
            )

    async def cancel(self, batch_id: str, message: str) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_batch_jobs
                SET status='cancelled', error=%s, updated_at=now(), finished_at=now()
                WHERE tenant_id=%s AND batch_id=%s AND status IN ('queued','running')""",
                (message[:1000], self.tenant_id, batch_id),
            )
            return (cursor.rowcount or 0) > 0

    async def begin_retry(
        self, batch_id: str, *, retry_keys: Sequence[str],
    ) -> Mapping[str, Any] | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_batch_jobs
                SET status='queued', retry_keys=%s, error=NULL, started_at=NULL,
                    finished_at=NULL, updated_at=now()
                WHERE tenant_id=%s AND batch_id=%s
                  AND status NOT IN ('queued','running')
                RETURNING *""",
                (_jsonb(list(retry_keys)), self.tenant_id, batch_id),
            )
            return await cursor.fetchone()


async def recover_interrupted_batches(connection_factory: Any) -> int:
    """启动时收敛遗留作业；本地单实例部署，其他实例仍在执行的作业会重新排队。"""
    connection = await connection_factory()
    async with connection:
        cursor = await connection.execute(
            """UPDATE platform_batch_jobs
            SET status='interrupted', error='platform restarted before the batch finished',
                updated_at=now(), finished_at=now()
            WHERE status IN ('queued','running')"""
        )
        return cursor.rowcount or 0


class BatchLocalConcurrencyLimiter(QuotaConcurrencyLimiter):
    """批次行不是平台 Run，无法满足配额预留表对 run_id 的 Run 外键约束。

    执行器本身已用 max_concurrency 限制并发，这里保留执行器要求的限流器
    契约但不写配额预留，批次并发只由 max_concurrency 控制。
    """

    def __init__(self) -> None:
        # 不初始化父类的配额依赖：本限流器不访问配额存储。
        pass

    @asynccontextmanager
    async def slot(
        self, *, tenant_id: str, run_id: str, scope_type: str, scope_id: str,
    ) -> AsyncIterator[None]:
        yield None


class BatchJobRunner:
    """进程内后台任务登记表；持久化状态仍然只写数据库。"""

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def spawn(self, batch_id: str, coroutine: Awaitable[None]) -> None:
        task = asyncio.create_task(coroutine, name=f"batch-job-{batch_id}")
        self._tasks[batch_id] = task

        def _discard(finished: asyncio.Task[None], key: str = batch_id) -> None:
            if self._tasks.get(key) is finished:
                self._tasks.pop(key, None)

        task.add_done_callback(_discard)

    def cancel(self, batch_id: str) -> bool:
        task = self._tasks.get(batch_id)
        if task is None or task.done():
            return False
        task.cancel()
        return True

    @property
    def active(self) -> int:
        return sum(1 for task in self._tasks.values() if not task.done())

    async def shutdown(self) -> None:
        tasks = [task for task in self._tasks.values() if not task.done()]
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


class PostgresBatchService:
    """单个租户/操作者视角的批次服务，实现 api.batch.BatchTaskService 协议。"""

    def __init__(
        self, *, tenant_id: str, actor_id: str,
        repository: PostgresBatchRepository,
        template_resolver: Callable[[str], Awaitable[BatchTemplate] | BatchTemplate],
        jobs: BatchJobRunner,
        task_timeout_seconds: float = 300.0,
    ) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id is required")
        if repository.tenant_id != tenant_id:
            raise ValueError("batch repository tenant mismatch")
        self.tenant_id = tenant_id
        self.actor_id = actor_id
        self.repository = repository
        self._limiter = BatchLocalConcurrencyLimiter()
        self._template_resolver = template_resolver
        self._jobs = jobs
        self._task_timeout_seconds = float(task_timeout_seconds)

    @staticmethod
    def _digest(
        template_id: str, dataset: Sequence[Mapping[str, Any]], max_concurrency: int,
    ) -> str:
        canonical = json.dumps(
            {
                "template_id": template_id,
                "dataset": [dict(row) for row in dataset],
                "max_concurrency": max_concurrency,
            },
            ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    async def _resolve_template(self, template_id: str) -> BatchTemplate:
        resolved = self._template_resolver(template_id)
        if inspect.isawaitable(resolved):
            resolved = await resolved
        if not isinstance(resolved, BatchTemplate):
            raise ValueError(f"unknown batch template: {template_id}")
        if resolved.template_id != template_id:
            raise ValueError("batch template resolver returned a mismatched template")
        return resolved

    def _check(self, tenant_id: str, actor_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise PermissionError("batch service tenant mismatch")
        if actor_id != self.actor_id:
            raise PermissionError("batch service actor mismatch")

    async def submit_batch(
        self, *, tenant_id: str, actor_id: str, template_id: str,
        dataset: Sequence[Mapping[str, Any]], max_concurrency: int,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]:
        self._check(tenant_id, actor_id)
        rows = [dict(row) for row in dataset]
        digest = self._digest(template_id, rows, max_concurrency)
        if idempotency_key is not None:
            existing = await self.repository.find_by_idempotency(idempotency_key)
            if existing is not None:
                if existing.get("request_digest") != digest:
                    raise ValueError(
                        "idempotency_key was already used with a different batch request"
                    )
                return _job_view(existing)
        template = await self._resolve_template(template_id)
        batch_id = str(uuid4())
        row = await self.repository.create(
            batch_id=batch_id, actor_id=actor_id, template_id=template_id,
            max_concurrency=max_concurrency, dataset=rows,
            idempotency_key=idempotency_key, request_digest=digest,
        )
        self._jobs.spawn(
            batch_id,
            self._run_batch(
                batch_id=batch_id, template=template, dataset=rows,
                indices=list(range(len(rows))), max_concurrency=max_concurrency,
            ),
        )
        return _job_view(row)

    async def _run_batch(
        self, *, batch_id: str, template: BatchTemplate,
        dataset: Sequence[Mapping[str, Any]], indices: Sequence[int],
        max_concurrency: int,
    ) -> None:
        executor = BatchExecutor(
            self._limiter, max_concurrency=max_concurrency,
            task_timeout_seconds=self._task_timeout_seconds,
        )
        try:
            started = await self.repository.mark_running(batch_id)
            if not started:
                # 已被取消或已被其他执行者接管，保持数据库现状。
                return
            rows = [dataset[index] for index in indices]
            result = await executor.execute(
                tenant_id=self.tenant_id, template=template,
                dataset=rows, batch_id=batch_id,
            )
            merged = await self._merge(batch_id, result, indices)
            succeeded = sum(1 for item in merged if item["status"] == "succeeded")
            failed = len(merged) - succeeded
            await self.repository.finish(
                batch_id,
                status="succeeded" if failed == 0 else "failed",
                succeeded=succeeded, failed=failed, results=merged,
            )
        except asyncio.CancelledError:
            # 取消状态已由 cancel_batch 落库，这里不再覆盖。
            raise
        except Exception as exc:
            await self.repository.fail(
                batch_id, f"{type(exc).__name__}: {str(exc)}",
            )

    async def _merge(
        self, batch_id: str, result: BatchExecutionResult, indices: Sequence[int],
    ) -> list[dict[str, Any]]:
        current = await self.repository.get(batch_id)
        existing = {
            int(item["index"]): dict(item)
            for item in ((current or {}).get("results") or [])
            if isinstance(item, Mapping) and "index" in item
        }
        for task in result.tasks:
            original_index = indices[task.index]
            existing[original_index] = _task_result_payload(task, original_index)
        return [existing[index] for index in sorted(existing)]

    async def list_batches(
        self, *, tenant_id: str, actor_id: str, limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]:
        self._check(tenant_id, actor_id)
        rows = await self.repository.list_jobs(actor_id=actor_id, limit=limit)
        return [_job_view(row, include_dataset=False) for row in rows]

    async def get_batch(
        self, *, tenant_id: str, batch_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None:
        self._check(tenant_id, actor_id)
        row = await self.repository.get(batch_id)
        return _job_view(row) if row is not None else None

    async def cancel_batch(
        self, *, tenant_id: str, batch_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None:
        self._check(tenant_id, actor_id)
        row = await self.repository.get(batch_id)
        if row is None:
            return None
        if row["status"] not in ACTIVE_BATCH_STATUSES:
            raise ValueError("batch is already finished")
        await self.repository.cancel(batch_id, "batch cancelled by operator")
        self._jobs.cancel(batch_id)
        refreshed = await self.repository.get(batch_id)
        return _job_view(refreshed) if refreshed is not None else None

    async def retry_batch(
        self, *, tenant_id: str, batch_id: str, actor_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any] | None:
        self._check(tenant_id, actor_id)
        row = await self.repository.get(batch_id)
        if row is None:
            return None
        if row["status"] in ACTIVE_BATCH_STATUSES:
            raise ValueError("batch is still running")
        retry_keys = [str(key) for key in (row.get("retry_keys") or [])]
        if idempotency_key in retry_keys:
            return _job_view(row)
        if await self.repository.retry_key_owned_elsewhere(idempotency_key, batch_id):
            raise ValueError("idempotency_key was already used by another batch")
        failed_indices = [
            int(item["index"]) for item in (row.get("results") or [])
            if isinstance(item, Mapping) and item.get("status") != "succeeded"
        ]
        dataset = [dict(item) for item in (row.get("dataset") or [])]
        missing = [index for index in range(len(dataset)) if index not in {
            int(item["index"]) for item in (row.get("results") or [])
            if isinstance(item, Mapping) and "index" in item
        }]
        targets = sorted(set(failed_indices) | set(missing))
        if not targets:
            raise ValueError("batch has no failed tasks to retry")
        updated = await self.repository.begin_retry(
            batch_id, retry_keys=[*retry_keys, idempotency_key],
        )
        if updated is None:
            raise ValueError("batch retry could not be started")
        template = await self._resolve_template(str(row["template_id"]))
        self._jobs.spawn(
            batch_id,
            self._run_batch(
                batch_id=batch_id, template=template, dataset=dataset,
                indices=targets, max_concurrency=int(row["max_concurrency"]),
            ),
        )
        return _job_view(updated)


async def await_task_terminal(
    task_service: Any, *, tenant_id: str, actor_id: str, task_id: str,
    timeout_seconds: float = 280.0, poll_interval_seconds: float = 1.0,
) -> Mapping[str, Any]:
    """轮询工作台任务直到终态；超时由批次执行器的行级超时兜底。"""
    deadline = time.monotonic() + timeout_seconds
    while True:
        view = await task_service.get_task(
            tenant_id=tenant_id, task_id=task_id, actor_id=actor_id,
        )
        if view is None:
            raise RuntimeError(f"task {task_id} disappeared")
        if view.get("status") in TERMINAL_TASK_STATUSES:
            return view
        if time.monotonic() >= deadline:
            raise TimeoutError(f"task {task_id} did not reach a terminal state in time")
        await asyncio.sleep(poll_interval_seconds)
