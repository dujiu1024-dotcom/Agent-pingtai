"""Durable human intervention, Run timeline and checkpoint resume requests."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID


ConnectionFactory = Callable[[], Any]
InterventionApplier = Callable[[Mapping[str, Any]], Awaitable[None]]


class RunInterventionService:
    def __init__(self, *, tenant_id: str, connection_factory: ConnectionFactory) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        self.tenant_id = tenant_id
        self._connect = connection_factory

    async def submit(
        self, *, tenant_id: str, run_id: str, actor_id: str,
        kind: str, payload: Mapping[str, Any],
    ) -> Mapping[str, Any]:
        if tenant_id != self.tenant_id:
            raise PermissionError("Run intervention tenant mismatch")
        if kind not in {"context", "decision", "takeover"}:
            raise ValueError("unsupported intervention kind")
        if not actor_id.strip() or not payload or len(repr(payload)) > 32_000:
            raise ValueError("actor and bounded non-empty intervention payload are required")
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        db = await self._connect()
        async with db:
            async with db.transaction():
                run = await db.execute(
                    """SELECT status FROM platform_runs
                       WHERE tenant_id=%s AND run_id=%s FOR UPDATE""",
                    (self.tenant_id, UUID(run_id)),
                )
                row = await run.fetchone()
                if row is None:
                    raise KeyError("Run not found")
                if row["status"] != "paused":
                    raise ValueError("human intervention requires a paused Run")
                inserted = await db.execute(
                    """INSERT INTO platform_run_interventions
                       (tenant_id,run_id,actor_id,kind,payload)
                       VALUES (%s,%s,%s,%s,%s)
                       RETURNING intervention_id,created_at,status""",
                    (self.tenant_id, UUID(run_id), actor_id, kind, Jsonb(dict(payload))),
                )
                item = await inserted.fetchone()
                await db.execute(
                    """INSERT INTO platform_run_events
                       (tenant_id,run_id,event_type,actor_id,payload)
                       VALUES (%s,%s,'human.intervention_submitted',%s,%s)""",
                    (self.tenant_id, UUID(run_id), actor_id,
                     Jsonb({"intervention_id": str(item["intervention_id"]), "kind": kind})),
                )
        return {
            "intervention_id": str(item["intervention_id"]),
            "run_id": run_id, "kind": kind, "status": item["status"],
            "created_at": item["created_at"].isoformat(),
        }

    async def list_interventions(self, *, tenant_id: str, run_id: str) -> list[Mapping[str, Any]]:
        if tenant_id != self.tenant_id:
            return []
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """SELECT intervention_id,actor_id,kind,payload,status,created_at,applied_at,applied_by
                   FROM platform_run_interventions
                   WHERE tenant_id=%s AND run_id=%s ORDER BY created_at,intervention_id""",
                (self.tenant_id, UUID(run_id)),
            )
            rows = await cursor.fetchall()
        return [{
            "intervention_id": str(row["intervention_id"]), "actor_id": row["actor_id"],
            "kind": row["kind"], "payload": dict(row["payload"]), "status": row["status"],
            "created_at": row["created_at"].isoformat(),
            "applied_at": row["applied_at"].isoformat() if row["applied_at"] else None,
            "applied_by": row["applied_by"],
        } for row in rows]

    async def list_events(self, *, tenant_id: str, run_id: str, limit: int = 200) -> list[Mapping[str, Any]]:
        if tenant_id != self.tenant_id:
            return []
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """SELECT event_id,event_type,actor_id,payload,occurred_at
                   FROM platform_run_events WHERE tenant_id=%s AND run_id=%s
                   ORDER BY occurred_at,event_id LIMIT %s""",
                (self.tenant_id, UUID(run_id), limit),
            )
            rows = await cursor.fetchall()
        return [{
            "event_id": str(row["event_id"]), "event_type": row["event_type"],
            "actor_id": row["actor_id"], "payload": dict(row["payload"]),
            "occurred_at": row["occurred_at"].isoformat(),
        } for row in rows]

    async def apply_pending(
        self, *, tenant_id: str, run_id: str, worker_id: str,
        applier: InterventionApplier,
    ) -> int:
        """Apply pending interventions under row locks before Run recovery."""
        if tenant_id != self.tenant_id or not worker_id.strip():
            raise PermissionError("invalid intervention worker context")
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        applied = 0
        while True:
            db = await self._connect()
            async with db:
                async with db.transaction():
                    cursor = await db.execute(
                        """SELECT intervention_id,actor_id,kind,payload
                           FROM platform_run_interventions
                           WHERE tenant_id=%s AND run_id=%s AND status='pending'
                           ORDER BY created_at,intervention_id
                           LIMIT 1 FOR UPDATE SKIP LOCKED""",
                        (self.tenant_id, UUID(run_id)),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        return applied
                    payload = {
                        "intervention_id": str(row["intervention_id"]),
                        "actor_id": row["actor_id"], "kind": row["kind"],
                        "payload": dict(row["payload"]),
                    }
                    # Keep the row lock until the durable graph checkpoint has
                    # accepted the intervention and the applied marker commits.
                    await applier(payload)
                    await db.execute(
                        """UPDATE platform_run_interventions
                           SET status='applied',applied_at=now(),applied_by=%s
                           WHERE tenant_id=%s AND intervention_id=%s AND status='pending'""",
                        (worker_id, self.tenant_id, row["intervention_id"]),
                    )
                    await db.execute(
                        """INSERT INTO platform_run_events
                           (tenant_id,run_id,event_type,actor_id,payload)
                           VALUES (%s,%s,'human.intervention_applied',%s,%s)""",
                        (self.tenant_id, UUID(run_id), worker_id,
                         Jsonb({"intervention_id": str(row["intervention_id"])})),
                    )
            applied += 1



class PostgresRunDetailService:
    def __init__(self, *, tenant_id: str, connection_factory: ConnectionFactory) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        self.tenant_id = tenant_id
        self._connect = connection_factory

    async def get(self, *, tenant_id: str, run_id: str) -> Mapping[str, Any] | None:
        if tenant_id != self.tenant_id:
            return None
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """SELECT tenant_id,run_id,project_id,task_id,agent_id,template_id,
                          template_version,workflow_id,status,input,output,usage,trace_id,
                          started_at,finished_at,created_at,updated_at,checkpoint_ref,last_error
                   FROM platform_runs WHERE tenant_id=%s AND run_id=%s""",
                (self.tenant_id, UUID(run_id)),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return {
            "tenant_id": row["tenant_id"], "run_id": str(row["run_id"]),
            "project_id": row["project_id"], "task_id": row["task_id"],
            "agent_id": row["agent_id"], "template_id": row["template_id"],
            "template_version": row["template_version"], "workflow_id": row["workflow_id"],
            "status": row["status"], "input": dict(row["input"]),
            "output": dict(row["output"]) if row["output"] is not None else None,
            "usage": dict(row["usage"]), "trace_id": row["trace_id"],
            "started_at": row["started_at"].isoformat() if row["started_at"] else None,
            "finished_at": row["finished_at"].isoformat() if row["finished_at"] else None,
            "created_at": row["created_at"].isoformat(),
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            "checkpoint_ref": row["checkpoint_ref"], "last_error": row["last_error"],
        }
