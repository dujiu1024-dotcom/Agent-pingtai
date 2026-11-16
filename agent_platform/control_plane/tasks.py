"""PostgreSQL-backed task center and lease-based queue worker."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID, uuid4

ConnectionFactory = Callable[[], Any]


@dataclass(frozen=True)
class TaskLease:
    tenant_id: str
    task_id: str
    actor_id: str
    agent_id: str
    project_id: str | None
    session_id: str | None
    input: Mapping[str, Any]
    attempts: int
    max_attempts: int
    lease_owner: str
    status: str = "running"


class TaskCenterError(ValueError):
    pass


class PostgresTaskCenterService:
    def __init__(
        self, *, tenant_id: str, connection_factory: ConnectionFactory,
        cancel_run: Callable[[str, str], Awaitable[Any]] | None = None,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        self.tenant_id = tenant_id
        self._connect = connection_factory
        self._cancel_run = cancel_run

    async def submit_task(
        self, *, tenant_id: str, actor_id: str, agent_id: str,
        project_id: str | None, session_id: str | None,
        input: Mapping[str, Any], idempotency_key: str | None = None,
        priority: int = 100,
    ) -> Mapping[str, Any]:
        if tenant_id != self.tenant_id:
            raise PermissionError("task tenant mismatch")
        if not actor_id.strip() or not agent_id.strip() or not input:
            raise ValueError("actor, agent and non-empty task input are required")
        if not isinstance(priority, int) or not 0 <= priority <= 1000:
            raise ValueError("priority must be between 0 and 1000")
        if idempotency_key is not None and (
            not idempotency_key.strip() or len(idempotency_key) > 200
        ):
            raise ValueError("idempotency_key must contain 1 to 200 characters")
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        task_id = uuid4()
        db = await self._connect()
        async with db:
            async with db.transaction():
                agent = await db.execute(
                    "SELECT status FROM platform_agents WHERE tenant_id=%s AND agent_id=%s FOR SHARE",
                    (self.tenant_id, agent_id),
                )
                row = await agent.fetchone()
                if row is None or row["status"] != "active":
                    raise PermissionError("Agent is not active or not visible")
                inserted = await db.execute(
                    """INSERT INTO platform_tasks
                       (tenant_id,task_id,actor_id,agent_id,project_id,session_id,input,priority,idempotency_key)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (tenant_id,actor_id,idempotency_key)
                       WHERE idempotency_key IS NOT NULL DO NOTHING
                       RETURNING task_id""",
                    (self.tenant_id, task_id, actor_id, agent_id, project_id,
                     UUID(session_id) if session_id else None, Jsonb(dict(input)), priority, idempotency_key),
                )
                created = await inserted.fetchone()
                if created is None:
                    existing = await db.execute(
                        """SELECT * FROM platform_tasks
                           WHERE tenant_id=%s AND actor_id=%s AND idempotency_key=%s FOR UPDATE""",
                        (self.tenant_id, actor_id, idempotency_key),
                    )
                    prior = await existing.fetchone()
                    if prior is None:
                        raise TaskCenterError("idempotent task could not be resolved")
                    if (
                        prior["agent_id"] != agent_id
                        or prior["project_id"] != project_id
                        or (str(prior["session_id"]) if prior["session_id"] else None) != session_id
                        or dict(prior["input"]) != dict(input)
                        or prior["priority"] != priority
                    ):
                        raise TaskCenterError("idempotency key was reused with different task input")
                    return self._serialize(prior)
                cursor = await db.execute(
                    "SELECT * FROM platform_tasks WHERE tenant_id=%s AND task_id=%s",
                    (self.tenant_id, task_id),
                )
                return self._serialize(await cursor.fetchone())

    async def list_tasks(
        self, *, tenant_id: str, actor_id: str, limit: int = 100,
    ) -> list[Mapping[str, Any]]:
        if tenant_id != self.tenant_id:
            return []
        if not 1 <= limit <= 200:
            raise ValueError("limit must be between 1 and 200")
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """SELECT * FROM platform_tasks WHERE tenant_id=%s AND actor_id=%s
                   ORDER BY updated_at DESC LIMIT %s""",
                (self.tenant_id, actor_id, limit),
            )
            return [self._serialize(row) for row in await cursor.fetchall()]

    async def get_task(self, *, tenant_id: str, task_id: str) -> Mapping[str, Any] | None:
        if tenant_id != self.tenant_id:
            return None
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                "SELECT * FROM platform_tasks WHERE tenant_id=%s AND task_id=%s",
                (self.tenant_id, UUID(task_id)),
            )
            row = await cursor.fetchone()
        return self._serialize(row) if row else None

    async def cancel_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None:
        if tenant_id != self.tenant_id:
            return None
        db = await self._connect()
        async with db:
            async with db.transaction():
                cursor = await db.execute(
                    """UPDATE platform_tasks SET
                         status=CASE WHEN status IN ('queued','paused') THEN 'cancelled'
                                     WHEN status='running' THEN 'cancelling' ELSE status END,
                         finished_at=CASE WHEN status IN ('queued','paused') THEN now() ELSE finished_at END,
                         updated_at=now()
                       WHERE tenant_id=%s AND task_id=%s AND actor_id=%s
                       RETURNING *""",
                    (self.tenant_id, UUID(task_id), actor_id),
                )
                row = await cursor.fetchone()
        if row is None:
            return None
        result = self._serialize(row)
        if row["status"] == "cancelling":
            if row["run_id"] and self._cancel_run:
                cancellation = await self._cancel_run(self.tenant_id, str(row["run_id"]))
                run_status = getattr(cancellation, "status", None)
                run_status = getattr(run_status, "value", run_status)
                if run_status == "cancelled":
                    await self.acknowledge_cancel_by_id(task_id=task_id)
            elif row["run_id"] and self._cancel_run is None:
                db = await self._connect()
                async with db:
                    await db.execute(
                        """UPDATE platform_tasks SET status='running',updated_at=now()
                           WHERE tenant_id=%s AND task_id=%s AND status='cancelling'""",
                        (self.tenant_id, UUID(task_id)),
                    )
                raise TaskCenterError("running task cancellation requires a Run cancellation handler")
            elif not row["run_id"]:
                await self.acknowledge_cancel_by_id(task_id=task_id)
            result = await self.get_task(tenant_id=self.tenant_id, task_id=task_id) or result
        return result

    async def acknowledge_cancel_by_id(self, *, task_id: str) -> None:
        db = await self._connect()
        async with db:
            await db.execute(
                """UPDATE platform_tasks SET status='cancelled',lease_owner=NULL,
                     lease_expires_at=NULL,finished_at=now(),updated_at=now()
                   WHERE tenant_id=%s AND task_id=%s AND status='cancelling'""",
                (self.tenant_id, UUID(task_id)),
            )

    async def acknowledge_cancel(self, lease: TaskLease) -> None:
        db = await self._connect()
        async with db:
            await db.execute(
                """UPDATE platform_tasks SET status='cancelled',lease_owner=NULL,
                     lease_expires_at=NULL,finished_at=now(),updated_at=now()
                   WHERE tenant_id=%s AND task_id=%s AND status='cancelling'
                     AND lease_owner=%s""",
                (self.tenant_id, UUID(lease.task_id), lease.lease_owner),
            )

    async def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> TaskLease | None:
        if not worker_id.strip() or lease_seconds < 5:
            raise ValueError("worker_id and lease_seconds >= 5 are required")
        db = await self._connect()
        async with db:
            async with db.transaction():
                cursor = await db.execute(
                    """WITH candidate AS (
                           SELECT tenant_id,task_id FROM platform_tasks
                           WHERE tenant_id=%s
                             AND ((status='queued' AND available_at<=now())
                               OR (status IN ('running','cancelling')
                                   AND (lease_expires_at IS NULL OR lease_expires_at<=now())))
                           ORDER BY priority DESC,available_at,created_at
                           LIMIT 1 FOR UPDATE SKIP LOCKED
                       )
                       UPDATE platform_tasks AS task
                       SET status=CASE WHEN task.status='cancelling' THEN 'cancelling' ELSE 'running' END,
                           attempts=task.attempts+CASE WHEN task.status='cancelling' THEN 0 ELSE 1 END,
                           lease_owner=%s,
                           lease_expires_at=now()+(%s * interval '1 second'),updated_at=now()
                       FROM candidate
                       WHERE task.tenant_id=candidate.tenant_id
                         AND task.task_id=candidate.task_id
                       RETURNING task.*""",
                    (self.tenant_id, worker_id, lease_seconds),
                )
                claimed = await cursor.fetchone()
                if claimed is None:
                    return None
        return TaskLease(
            tenant_id=claimed["tenant_id"], task_id=str(claimed["task_id"]),
            actor_id=claimed["actor_id"], agent_id=claimed["agent_id"],
            project_id=claimed["project_id"],
            session_id=str(claimed["session_id"]) if claimed["session_id"] else None,
            input=dict(claimed["input"]), attempts=claimed["attempts"],
            max_attempts=claimed["max_attempts"], lease_owner=worker_id,
            status=claimed["status"],
        )

    async def set_run_id(self, lease: TaskLease, run_id: str) -> None:
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_tasks SET run_id=%s,updated_at=now()
                   WHERE tenant_id=%s AND task_id=%s AND status='running' AND lease_owner=%s
                   RETURNING task_id""",
                (UUID(run_id), self.tenant_id, UUID(lease.task_id), lease.lease_owner),
            )
            if await cursor.fetchone() is None:
                raise TaskCenterError("task lease was lost before Run binding")

    async def complete(self, lease: TaskLease, *, output: Mapping[str, Any]) -> None:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_tasks SET status='succeeded',output=%s,
                     lease_owner=NULL,lease_expires_at=NULL,finished_at=now(),updated_at=now()
                   WHERE tenant_id=%s AND task_id=%s AND status='running' AND lease_owner=%s
                   RETURNING task_id""",
                (Jsonb(dict(output)), self.tenant_id, UUID(lease.task_id), lease.lease_owner),
            )
            if await cursor.fetchone() is None:
                raise TaskCenterError("task lease was lost before completion")

    async def heartbeat(self, lease: TaskLease, *, lease_seconds: int = 60) -> bool:
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least 5")
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """UPDATE platform_tasks SET lease_expires_at=now()+(%s * interval '1 second')
                   WHERE tenant_id=%s AND task_id=%s AND status IN ('running','cancelling')
                     AND lease_owner=%s AND lease_expires_at>now()
                   RETURNING task_id""",
                (lease_seconds, self.tenant_id, UUID(lease.task_id), lease.lease_owner),
            )
            return await cursor.fetchone() is not None

    async def fail(self, lease: TaskLease, *, error: str) -> None:
        dead = lease.attempts >= lease.max_attempts
        delay = min(1800, 2 ** min(lease.attempts, 10))
        db = await self._connect()
        async with db:
            await db.execute(
                """UPDATE platform_tasks SET status=%s,last_error=%s,
                     available_at=now()+(%s * interval '1 second'),
                     lease_owner=NULL,lease_expires_at=NULL,
                     finished_at=CASE WHEN %s THEN now() ELSE finished_at END,
                     updated_at=now()
                   WHERE tenant_id=%s AND task_id=%s AND status='running' AND lease_owner=%s""",
                ("failed" if dead else "queued", error[:500], delay, dead,
                 self.tenant_id, UUID(lease.task_id), lease.lease_owner),
            )

    @staticmethod
    def _serialize(row: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "task_id": str(row["task_id"]), "actor_id": row["actor_id"],
            "agent_id": row["agent_id"], "project_id": row["project_id"],
            "session_id": str(row["session_id"]) if row["session_id"] else None,
            "run_id": str(row["run_id"]) if row["run_id"] else None,
            "status": row["status"], "priority": row["priority"],
            "attempts": row["attempts"], "input": dict(row["input"]),
            "output": dict(row["output"]) if row["output"] is not None else None,
            "last_error": row["last_error"],
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat(),
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        }


class TaskQueueWorker:
    """One-shot queue consumer; host callback starts the existing Run executor."""

    def __init__(
        self, *, service: PostgresTaskCenterService, worker_id: str,
        execute_task: Callable[[TaskLease], Awaitable[Mapping[str, Any]]],
        lease_seconds: int = 60,
    ) -> None:
        self.service = service
        self.worker_id = worker_id
        self.execute_task = execute_task
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least 5")
        self.lease_seconds = lease_seconds

    async def run_once(self) -> bool:
        lease = await self.service.claim_next(
            worker_id=self.worker_id, lease_seconds=self.lease_seconds,
        )
        if lease is None:
            return False
        if lease.status == "cancelling":
            await self.service.acknowledge_cancel(lease)
            return True
        execution = asyncio.create_task(self.execute_task(lease))
        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(max(1, self.lease_seconds / 3))
                if not await self.service.heartbeat(lease, lease_seconds=self.lease_seconds):
                    execution.cancel()
                    raise TaskCenterError("task worker lease was lost")
        beat = asyncio.create_task(heartbeat())
        try:
            done, _ = await asyncio.wait({execution, beat}, return_when=asyncio.FIRST_COMPLETED)
            if beat in done:
                await beat
            result = dict(await execution)
            run_id = result.pop("run_id", None)
            if run_id:
                await self.service.set_run_id(lease, str(run_id))
            await self.service.complete(lease, output=result)
        except asyncio.CancelledError:
            execution.cancel()
            await asyncio.gather(execution, return_exceptions=True)
            await self.service.acknowledge_cancel(lease)
            raise
        except Exception as exc:
            await self.service.fail(lease, error=type(exc).__name__)
        finally:
            beat.cancel()
            await asyncio.gather(beat, return_exceptions=True)
        return True
