"""Durable human intervention, Run timeline and checkpoint resume requests."""
from __future__ import annotations

import json
from typing import Any, Awaitable, Callable, Mapping
from uuid import UUID

from agent_platform.observability.redaction import redact_payload

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
        if kind not in {"context", "decision", "approval", "takeover"}:
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
                       VALUES (%s,%s,%s,%s,%s)""",
                    (self.tenant_id, UUID(run_id),
                     "human.takeover_requested" if kind == "takeover" else "human.intervention_submitted",
                     actor_id, Jsonb({"intervention_id": str(item["intervention_id"]), "kind": kind})),
                )
        return {
            "intervention_id": str(item["intervention_id"]),
            "run_id": run_id, "kind": kind, "status": item["status"],
            "created_at": item["created_at"].isoformat(),
        }


    async def decide(
        self, *, tenant_id: str, run_id: str, intervention_id: str,
        actor_id: str, decision: str, rationale: str = "",
    ) -> Mapping[str, Any]:
        """Record an explicit human decision against a pending decision/approval."""
        if tenant_id != self.tenant_id:
            raise PermissionError("Run intervention tenant mismatch")
        if decision not in {"approved", "rejected", "needs_changes"}:
            raise ValueError("decision must be approved, rejected or needs_changes")
        if not actor_id.strip() or len(rationale) > 4000:
            raise ValueError("actor_id and bounded rationale are required")
        safe_rationale = redact_payload({"rationale": rationale}).get("rationale", "")
        from psycopg.types.json import Jsonb
        db = await self._connect()
        async with db:
            async with db.transaction():
                cursor = await db.execute(
                    """SELECT kind,status,payload FROM platform_run_interventions
                       WHERE tenant_id=%s AND run_id=%s AND intervention_id=%s FOR UPDATE""",
                    (self.tenant_id, UUID(run_id), UUID(intervention_id)),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError("intervention not found")
                if row["kind"] not in {"decision", "approval"}:
                    raise ValueError("only decision or approval interventions can be decided")
                if row["status"] != "pending" or "decision" in dict(row["payload"]):
                    raise ValueError("intervention has already been resolved")
                await db.execute(
                    """UPDATE platform_run_interventions
                       SET status=%s,payload=payload || %s,
                           applied_at=CASE WHEN %s='rejected' THEN now() ELSE NULL END,
                           applied_by=CASE WHEN %s='rejected' THEN %s ELSE NULL END
                       WHERE tenant_id=%s AND run_id=%s AND intervention_id=%s AND status='pending'""",
                    ("rejected" if decision == "rejected" else "pending",
                     Jsonb({"decision": decision, "rationale": safe_rationale}),
                     decision, decision, actor_id, self.tenant_id,
                     UUID(run_id), UUID(intervention_id)),
                )
                await db.execute(
                    """INSERT INTO platform_run_events
                       (tenant_id,run_id,event_type,actor_id,payload)
                       VALUES (%s,%s,'human.intervention_decided',%s,%s)""",
                    (self.tenant_id, UUID(run_id), actor_id, Jsonb({
                        "intervention_id": intervention_id,
                        "decision": decision,
                        "rationale": safe_rationale,
                    })),
                )
        return {
            "intervention_id": intervention_id, "run_id": run_id,
            "decision": decision, "status": "rejected" if decision == "rejected" else "pending", "decided_by": actor_id,
        }

    async def revise_result(
        self, *, tenant_id: str, run_id: str, actor_id: str,
        output: Mapping[str, Any], reason: str,
    ) -> Mapping[str, Any]:
        """Replace a completed Run result with an audited, bounded human revision."""
        if tenant_id != self.tenant_id:
            raise PermissionError("Run result tenant mismatch")
        if not actor_id.strip() or not isinstance(output, Mapping) or not output:
            raise ValueError("actor and non-empty output object are required")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            raise ValueError("revision reason must contain 1 to 4000 characters")
        safe_output = redact_payload(dict(output), max_text_chars=4000)
        import json
        try:
            encoded = json.dumps(safe_output, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("revised output must be JSON serializable") from exc
        if len(encoded) > 32_000:
            raise ValueError("revised output exceeds 32 KB")
        from psycopg.types.json import Jsonb
        db = await self._connect()
        async with db:
            async with db.transaction():
                cursor = await db.execute(
                    """SELECT status,output FROM platform_runs
                       WHERE tenant_id=%s AND run_id=%s FOR UPDATE""",
                    (self.tenant_id, UUID(run_id)),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError("Run not found")
                if row["status"] != "succeeded":
                    raise ValueError("only a succeeded Run result can be revised")
                previous = row["output"]
                await db.execute(
                    """UPDATE platform_runs SET output=%s,updated_at=now()
                       WHERE tenant_id=%s AND run_id=%s""",
                    (Jsonb(safe_output), self.tenant_id, UUID(run_id)),
                )
                await db.execute(
                    """INSERT INTO platform_run_events
                       (tenant_id,run_id,event_type,actor_id,payload)
                       VALUES (%s,%s,'human.result_revised',%s,%s)""",
                    (self.tenant_id, UUID(run_id), actor_id, Jsonb({
                        "reason": redact_payload({"reason": reason}).get("reason", ""),
                        "previous_output": redact_payload(previous or {}, max_text_chars=4000),
                        "revised_output": safe_output,
                    })),
                )
        return {
            "run_id": run_id, "status": "succeeded", "output": safe_output,
            "revised_by": actor_id,
        }


    async def ensure_decisions_resolved(
        self, *, tenant_id: str, run_id: str,
    ) -> None:
        """Prevent continuation while an approval/decision still awaits a human."""
        if tenant_id != self.tenant_id:
            raise PermissionError("Run intervention tenant mismatch")
        db = await self._connect()
        async with db:
            cursor = await db.execute(
                """SELECT 1 FROM platform_run_interventions
                   WHERE tenant_id=%s AND run_id=%s
                     AND ((status='pending' AND kind IN ('decision','approval')
                           AND NOT (payload ? 'decision'))
                       OR status='rejected')
                   LIMIT 1""",
                (self.tenant_id, UUID(run_id)),
            )
            if await cursor.fetchone() is not None:
                raise ValueError("Run has a pending or rejected human decision/approval")

    async def record_action(
        self, *, tenant_id: str, run_id: str, actor_id: str,
        event_type: str, payload: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        """Append an operator action to the tenant-scoped Run audit timeline."""
        if tenant_id != self.tenant_id:
            raise PermissionError("Run action tenant mismatch")
        if event_type not in {
            "human.run_resumed", "human.run_cancel_requested", "human.run_paused",
        }:
            raise ValueError("unsupported human Run action")
        if not actor_id.strip():
            raise ValueError("actor_id is required")
        from psycopg.types.json import Jsonb
        safe_payload = redact_payload(dict(payload or {}), max_text_chars=4000)
        db = await self._connect()
        async with db:
            async with db.transaction():
                exists = await db.execute(
                    "SELECT 1 FROM platform_runs WHERE tenant_id=%s AND run_id=%s FOR KEY SHARE",
                    (self.tenant_id, UUID(run_id)),
                )
                if await exists.fetchone() is None:
                    raise KeyError("Run not found")
                cursor = await db.execute(
                    """INSERT INTO platform_run_events
                       (tenant_id,run_id,event_type,actor_id,payload)
                       VALUES (%s,%s,%s,%s,%s)
                       RETURNING event_id,occurred_at""",
                    (self.tenant_id, UUID(run_id), event_type, actor_id, Jsonb(safe_payload)),
                )
                row = await cursor.fetchone()
        return {
            "event_id": str(row["event_id"]), "run_id": run_id,
            "event_type": event_type, "actor_id": actor_id,
            "payload": safe_payload, "occurred_at": row["occurred_at"].isoformat(),
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

    async def record_event(
        self, *, tenant_id: str, run_id: str, event_type: str,
        payload: Mapping[str, Any], actor_id: str | None = None,
    ) -> Mapping[str, Any]:
        """Persist a bounded, redacted Run step/log/artifact event."""
        if tenant_id != self.tenant_id:
            raise PermissionError("Run event tenant mismatch")
        if event_type not in {
            "run.step.started", "run.step.completed", "run.step.failed",
            "run.log", "run.artifact.created",
        }:
            raise ValueError("unsupported Run event type")
        if not isinstance(payload, Mapping) or not payload:
            raise ValueError("Run event payload must be a non-empty object")
        if actor_id is not None and (not isinstance(actor_id, str) or not actor_id.strip()):
            raise ValueError("event actor_id must be a non-empty string")
        if event_type.startswith("run.step.") and not (
            isinstance(payload.get("step_id"), str) and payload["step_id"].strip()
        ):
            raise ValueError("step events require step_id")
        if event_type == "run.log":
            if payload.get("level") not in {"debug", "info", "warning", "error"}:
                raise ValueError("Run log level is invalid")
            if not isinstance(payload.get("message"), str) or not payload["message"].strip():
                raise ValueError("Run log message is required")
        if event_type == "run.artifact.created" and not (
            isinstance(payload.get("artifact_id"), str) and payload["artifact_id"].strip()
        ):
            raise ValueError("artifact events require artifact_id")
        safe_payload = redact_payload(dict(payload), max_text_chars=4000)
        try:
            encoded = json.dumps(safe_payload, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Run event payload must be JSON serializable") from exc
        if len(encoded) > 32_000:
            raise ValueError("Run event payload exceeds 32 KB")
        from psycopg.types.json import Jsonb
        db = await self._connect()
        async with db:
            async with db.transaction():
                exists = await db.execute(
                    "SELECT 1 FROM platform_runs WHERE tenant_id=%s AND run_id=%s FOR KEY SHARE",
                    (self.tenant_id, UUID(run_id)),
                )
                if await exists.fetchone() is None:
                    raise KeyError("Run not found")
                cursor = await db.execute(
                    """INSERT INTO platform_run_events
                       (tenant_id,run_id,event_type,actor_id,payload)
                       VALUES (%s,%s,%s,%s,%s)
                       RETURNING event_id,occurred_at""",
                    (self.tenant_id, UUID(run_id), event_type, actor_id, Jsonb(safe_payload)),
                )
                row = await cursor.fetchone()
        return {
            "event_id": str(row["event_id"]), "run_id": run_id,
            "event_type": event_type, "actor_id": actor_id,
            "payload": safe_payload, "occurred_at": row["occurred_at"].isoformat(),
        }

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
                             AND (kind NOT IN ('decision','approval') OR payload ? 'decision')
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
            event_cursor = await db.execute(
                """SELECT event_id,event_type,actor_id,payload,occurred_at
                   FROM platform_run_events WHERE tenant_id=%s AND run_id=%s
                   ORDER BY occurred_at DESC,event_id DESC LIMIT 500""",
                (self.tenant_id, UUID(run_id)),
            )
            event_rows = list(reversed(await event_cursor.fetchall()))
            approval_cursor = await db.execute(
                """SELECT approval_id,action,resource,risk_level,requested_by,
                          decided_by,status,expires_at
                   FROM platform_approvals WHERE tenant_id=%s AND run_id=%s
                   ORDER BY expires_at,approval_id""",
                (self.tenant_id, UUID(run_id)),
            )
            approval_rows = await approval_cursor.fetchall()
        events = [{
            "event_id": str(item["event_id"]), "event_type": item["event_type"],
            "actor_id": item["actor_id"], "payload": dict(item["payload"]),
            "occurred_at": item["occurred_at"].isoformat(),
        } for item in event_rows]
        approvals = [{
            "approval_id": str(item["approval_id"]), "action": item["action"],
            "resource": item["resource"], "risk_level": item["risk_level"],
            "requested_by": item["requested_by"], "decided_by": item["decided_by"],
            "status": item["status"], "expires_at": item["expires_at"].isoformat(),
        } for item in approval_rows]
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
            "events": events,
            "steps": [event for event in events if event["event_type"].startswith("run.step.")],
            "logs": [event for event in events if event["event_type"] == "run.log"],
            "artifacts": [event for event in events if event["event_type"] == "run.artifact.created"],
            "approvals": approvals,
        }
