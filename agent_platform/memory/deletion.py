"""Durable, tenant-scoped propagation of memory deletion to derived stores.

The memory row is tombstoned before this queue is enqueued. Cleanup handlers must
be idempotent and remove all derived index/cache entries for the exact tenant and
memory identity; this module never loads or copies memory content.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import uuid4


@dataclass(frozen=True)
class MemoryDeletionJob:
    job_id: str
    tenant_id: str
    memory_id: str
    attempts: int
    lease_token: str
    lease_expires_at: datetime


class PostgresMemoryDeletionQueue:
    """PostgreSQL outbox/lease queue for index and cache invalidation."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def enqueue(self, tenant_id: str, memory_id: str) -> bool:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(memory_id, str) or not memory_id.strip():
            raise ValueError("memory_id is required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_memory_deletion_jobs
                   (tenant_id, memory_id, status)
                   VALUES (%s, %s, 'pending')
                   ON CONFLICT (tenant_id, memory_id) DO NOTHING
                   RETURNING job_id""",
                (tenant_id, memory_id),
            )
            return await cursor.fetchone() is not None

    async def claim(
        self, *, worker_id: str, lease_seconds: int = 60,
    ) -> MemoryDeletionJob | None:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        token = str(uuid4())
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """WITH candidate AS (
                         SELECT job_id
                         FROM platform_memory_deletion_jobs
                         WHERE (
                             status IN ('pending', 'retry')
                             AND next_attempt_at <= now()
                         ) OR (
                             status = 'leased' AND lease_expires_at <= now()
                         )
                         ORDER BY created_at, job_id
                         FOR UPDATE SKIP LOCKED
                         LIMIT 1
                       )
                       UPDATE platform_memory_deletion_jobs AS job
                       SET status = 'leased',
                           lease_owner = %s,
                           lease_token = %s,
                           lease_expires_at = now() + (%s * interval '1 second'),
                           attempts = attempts + 1,
                           updated_at = now()
                       FROM candidate
                       WHERE job.job_id = candidate.job_id
                       RETURNING job.job_id, job.tenant_id, job.memory_id,
                                 job.attempts, job.lease_token, job.lease_expires_at""",
                    (worker_id, token, lease_seconds),
                )
                row = await cursor.fetchone()
        if row is None:
            return None
        return MemoryDeletionJob(
            job_id=str(row["job_id"]),
            tenant_id=row["tenant_id"],
            memory_id=row["memory_id"],
            attempts=row["attempts"],
            lease_token=str(row["lease_token"]),
            lease_expires_at=row["lease_expires_at"],
        )

    async def complete(self, *, job_id: str, lease_token: str) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_memory_deletion_jobs
                   SET status = 'completed', lease_owner = NULL,
                       lease_token = NULL, lease_expires_at = NULL,
                       last_error_code = NULL, updated_at = now()
                   WHERE job_id = %s AND status = 'leased' AND lease_token = %s""",
                (job_id, lease_token),
            )
            return cursor.rowcount == 1

    async def fail(
        self, *, job_id: str, lease_token: str, attempts: int,
        error_code: str, max_attempts: int = 8,
        base_delay_seconds: int = 5, max_delay_seconds: int = 3600,
    ) -> bool:
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
            raise ValueError("attempts must be a positive integer")
        if not isinstance(error_code, str) or not error_code or len(error_code) > 100:
            raise ValueError("error_code must be a short non-empty identifier")
        if not 1 <= max_attempts <= 100 or base_delay_seconds < 1 or max_delay_seconds < base_delay_seconds:
            raise ValueError("invalid retry policy")
        terminal = attempts >= max_attempts
        delay = min(max_delay_seconds, base_delay_seconds * (2 ** min(attempts - 1, 20)))
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_memory_deletion_jobs
                   SET status = %s,
                       next_attempt_at = now() + (%s * interval '1 second'),
                       lease_owner = NULL, lease_token = NULL,
                       lease_expires_at = NULL, last_error_code = %s,
                       updated_at = now()
                   WHERE job_id = %s AND status = 'leased' AND lease_token = %s""",
                ("dead" if terminal else "retry", delay, error_code, job_id, lease_token),
            )
            return cursor.rowcount == 1


class MemoryDeletionWorker:
    """Runs idempotent derived-data cleanup under a fenced queue lease."""

    def __init__(
        self, *, queue: PostgresMemoryDeletionQueue,
        cleanup: Callable[[str, str], Awaitable[None]],
        worker_id: str, max_attempts: int = 8, lease_seconds: int = 60,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id is required")
        if not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100")
        self.queue = queue
        self.cleanup = cleanup
        self.worker_id = worker_id
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds

    async def run_once(self) -> bool:
        job = await self.queue.claim(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds,
        )
        if job is None:
            return False
        try:
            result = self.cleanup(job.tenant_id, job.memory_id)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            # Persist only the exception type, never provider messages or memory data.
            await self.queue.fail(
                job_id=job.job_id, lease_token=job.lease_token,
                attempts=job.attempts, error_code=type(exc).__name__,
                max_attempts=self.max_attempts,
            )
            return True
        await self.queue.complete(job_id=job.job_id, lease_token=job.lease_token)
        return True
