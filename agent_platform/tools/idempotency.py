"""Durable idempotency ledger for externally visible tool side effects.

A claimed effect is never automatically retried after an ambiguous failure. Such
records require reconciliation, preventing a worker restart from repeating a
payment, message, deployment, or other irreversible operation.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Protocol


class EffectConflict(RuntimeError):
    pass


class EffectAlreadyStarted(RuntimeError):
    pass


@dataclass(frozen=True)
class ToolEffect:
    tenant_id: str
    run_id: str
    call_id: str
    action_digest: str
    actor_id: str
    state: str
    result: Any = None
    error: str | None = None
    created_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    updated_at: datetime = datetime.min.replace(tzinfo=timezone.utc)
    created: bool = False
    resolution: str | None = None
    reconciled_by: str | None = None
    reconciliation_note: str | None = None
    reconciled_at: datetime | None = None


class ToolEffectStore(Protocol):
    async def get(self, *, tenant_id: str, run_id: str, call_id: str) -> ToolEffect | None: ...
    async def claim(
        self, *, tenant_id: str, run_id: str, call_id: str,
        action_digest: str, actor_id: str,
    ) -> ToolEffect: ...
    async def complete(self, *, tenant_id: str, run_id: str, call_id: str, result: Any) -> None: ...
    async def fail(self, *, tenant_id: str, run_id: str, call_id: str, error: str) -> None: ...
    async def reconcile(
        self, *, tenant_id: str, run_id: str, call_id: str,
        operator_id: str, resolution: str, note: str, result: Any = None,
    ) -> ToolEffect: ...


class InMemoryToolEffectStore:
    """Test adapter with the same no-retry-after-claim semantics as PostgreSQL."""
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, str], ToolEffect] = {}

    async def get(self, *, tenant_id: str, run_id: str, call_id: str) -> ToolEffect | None:
        row = self._rows.get((tenant_id, run_id, call_id))
        return copy.deepcopy(row) if row else None

    async def claim(
        self, *, tenant_id: str, run_id: str, call_id: str,
        action_digest: str, actor_id: str,
    ) -> ToolEffect:
        key = (tenant_id, run_id, call_id)
        old = self._rows.get(key)
        if old is not None:
            if old.action_digest != action_digest or old.actor_id != actor_id:
                raise EffectConflict("idempotency key was reused for a different action")
            from dataclasses import replace
            if old.state == "retryable":
                old = replace(old, state="pending", error=None, updated_at=datetime.now(timezone.utc))
                self._rows[key] = old
                return copy.deepcopy(replace(old, created=True))
            return copy.deepcopy(replace(old, created=False))
        now = datetime.now(timezone.utc)
        row = ToolEffect(tenant_id, run_id, call_id, action_digest, actor_id, "pending",
                         created_at=now, updated_at=now, created=True)
        self._rows[key] = row
        return copy.deepcopy(row)

    async def complete(self, *, tenant_id: str, run_id: str, call_id: str, result: Any) -> None:
        from dataclasses import replace
        key = (tenant_id, run_id, call_id)
        row = self._rows.get(key)
        if row is None or row.state != "pending":
            raise EffectConflict("tool effect is not pending")
        self._rows[key] = replace(row, state="succeeded", result=copy.deepcopy(result),
                                  updated_at=datetime.now(timezone.utc))

    async def fail(self, *, tenant_id: str, run_id: str, call_id: str, error: str) -> None:
        from dataclasses import replace
        key = (tenant_id, run_id, call_id)
        row = self._rows.get(key)
        if row is not None and row.state == "pending":
            self._rows[key] = replace(row, state="failed", error=error[:500],
                                      updated_at=datetime.now(timezone.utc))

    async def reconcile(
        self, *, tenant_id: str, run_id: str, call_id: str,
        operator_id: str, resolution: str, note: str, result: Any = None,
    ) -> ToolEffect:
        from dataclasses import replace
        key = (tenant_id, run_id, call_id)
        row = self._rows.get(key)
        if row is None or row.state not in {"pending", "failed"}:
            raise EffectConflict("tool effect is not awaiting reconciliation")
        state = {
            "confirmed_succeeded": "succeeded",
            "confirmed_not_applied": "retryable",
            "compensated": "compensated",
        }.get(resolution)
        if state is None:
            raise ValueError("unsupported tool effect reconciliation resolution")
        if not operator_id.strip() or not note.strip() or len(note) > 1000:
            raise ValueError("operator_id and a 1-1000 character reconciliation note are required")
        if resolution == "confirmed_succeeded" and result is None:
            raise ValueError("confirmed_succeeded requires the externally observed result")
        now = datetime.now(timezone.utc)
        updated = replace(
            row, state=state,
            result=copy.deepcopy(result) if resolution == "confirmed_succeeded" else row.result,
            error=None if state == "succeeded" else row.error,
            resolution=resolution, reconciled_by=operator_id,
            reconciliation_note=note, reconciled_at=now, updated_at=now,
        )
        self._rows[key] = updated
        return copy.deepcopy(updated)


class PostgresToolEffectStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def get(self, *, tenant_id: str, run_id: str, call_id: str) -> ToolEffect | None:
        db = await self._connect()
        async with db:
            cur = await db.execute(
                """SELECT tenant_id,run_id,call_id,action_digest,actor_id,state,
                          result,error,created_at,updated_at,resolution,reconciled_by,reconciliation_note,reconciled_at
                   FROM platform_tool_effects
                   WHERE tenant_id=%s AND run_id=%s AND call_id=%s""",
                (tenant_id, run_id, call_id),
            )
            row = await cur.fetchone()
        return self._to_effect(row) if row else None

    async def claim(
        self, *, tenant_id: str, run_id: str, call_id: str,
        action_digest: str, actor_id: str,
    ) -> ToolEffect:
        db = await self._connect()
        async with db:
            async with db.transaction():
                inserted = await db.execute(
                    """INSERT INTO platform_tool_effects
                       (tenant_id,run_id,call_id,action_digest,actor_id,state)
                       VALUES (%s,%s,%s,%s,%s,'pending')
                       ON CONFLICT (tenant_id,run_id,call_id) DO NOTHING
                       RETURNING call_id""",
                    (tenant_id, run_id, call_id, action_digest, actor_id),
                )
                created = await inserted.fetchone() is not None
                cur = await db.execute(
                    """SELECT tenant_id,run_id,call_id,action_digest,actor_id,state,
                              result,error,created_at,updated_at,resolution,reconciled_by,reconciliation_note,reconciled_at
                       FROM platform_tool_effects
                       WHERE tenant_id=%s AND run_id=%s AND call_id=%s FOR UPDATE""",
                    (tenant_id, run_id, call_id),
                )
                row = await cur.fetchone()
                if row["state"] == "retryable":
                    await db.execute(
                        """UPDATE platform_tool_effects
                           SET state='pending',error=NULL,updated_at=now()
                           WHERE tenant_id=%s AND run_id=%s AND call_id=%s AND state='retryable'""",
                        (tenant_id, run_id, call_id),
                    )
                    cur = await db.execute(
                        """SELECT tenant_id,run_id,call_id,action_digest,actor_id,state,
                                  result,error,created_at,updated_at,resolution,reconciled_by,
                                  reconciliation_note,reconciled_at
                           FROM platform_tool_effects
                           WHERE tenant_id=%s AND run_id=%s AND call_id=%s FOR UPDATE""",
                        (tenant_id, run_id, call_id),
                    )
                    row = await cur.fetchone()
                    created = True
        effect = self._to_effect(row)
        if effect.action_digest != action_digest or effect.actor_id != actor_id:
            raise EffectConflict("idempotency key was reused for a different action")
        from dataclasses import replace
        return replace(effect, created=created)

    async def complete(self, *, tenant_id: str, run_id: str, call_id: str, result: Any) -> None:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        db = await self._connect()
        async with db:
            cur = await db.execute(
                """UPDATE platform_tool_effects
                   SET state='succeeded',result=%s,updated_at=now()
                   WHERE tenant_id=%s AND run_id=%s AND call_id=%s AND state='pending'
                   RETURNING call_id""",
                (Jsonb(result), tenant_id, run_id, call_id),
            )
            if await cur.fetchone() is None:
                raise EffectConflict("tool effect is not pending")

    async def fail(self, *, tenant_id: str, run_id: str, call_id: str, error: str) -> None:
        db = await self._connect()
        async with db:
            await db.execute(
                """UPDATE platform_tool_effects
                   SET state='failed',error=%s,updated_at=now()
                   WHERE tenant_id=%s AND run_id=%s AND call_id=%s AND state='pending'""",
                (error[:500], tenant_id, run_id, call_id),
            )

    async def reconcile(
        self, *, tenant_id: str, run_id: str, call_id: str,
        operator_id: str, resolution: str, note: str, result: Any = None,
    ) -> ToolEffect:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        state = {
            "confirmed_succeeded": "succeeded",
            "confirmed_not_applied": "retryable",
            "compensated": "compensated",
        }.get(resolution)
        if state is None:
            raise ValueError("unsupported tool effect reconciliation resolution")
        if not operator_id.strip() or not note.strip() or len(note) > 1000:
            raise ValueError("operator_id and a 1-1000 character reconciliation note are required")
        if resolution == "confirmed_succeeded" and result is None:
            raise ValueError("confirmed_succeeded requires the externally observed result")
        db = await self._connect()
        async with db:
            async with db.transaction():
                cur = await db.execute(
                    """SELECT tenant_id,run_id,call_id,action_digest,actor_id,state,
                              result,error,created_at,updated_at,resolution,reconciled_by,
                              reconciliation_note,reconciled_at
                       FROM platform_tool_effects
                       WHERE tenant_id=%s AND run_id=%s AND call_id=%s FOR UPDATE""",
                    (tenant_id, run_id, call_id),
                )
                old = await cur.fetchone()
                if old is None or old["state"] not in {"pending", "failed"}:
                    raise EffectConflict("tool effect is not awaiting reconciliation")
                cur = await db.execute(
                    """UPDATE platform_tool_effects
                       SET state=%s,result=CASE WHEN %s='confirmed_succeeded' THEN %s ELSE result END,
                           error=CASE WHEN %s='confirmed_succeeded' THEN NULL ELSE error END,
                           resolution=%s,reconciled_by=%s,reconciliation_note=%s,
                           reconciled_at=now(),updated_at=now()
                       WHERE tenant_id=%s AND run_id=%s AND call_id=%s
                       RETURNING tenant_id,run_id,call_id,action_digest,actor_id,state,
                                 result,error,created_at,updated_at,resolution,reconciled_by,
                                 reconciliation_note,reconciled_at""",
                    (state, resolution, Jsonb(result) if result is not None else None,
                     resolution, resolution, operator_id, note,
                     tenant_id, run_id, call_id),
                )
                updated = await cur.fetchone()
                await db.execute(
                    """INSERT INTO platform_tool_effect_reconciliations
                       (tenant_id,run_id,call_id,operator_id,resolution,note,evidence)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id, run_id, call_id, operator_id, resolution, note,
                     Jsonb(result) if result is not None else None),
                )
        return self._to_effect(updated)

    @staticmethod
    def _to_effect(row: Mapping[str, Any]) -> ToolEffect:
        return ToolEffect(
            tenant_id=row["tenant_id"], run_id=str(row["run_id"]),
            call_id=row["call_id"], action_digest=row["action_digest"],
            actor_id=row["actor_id"], state=row["state"], result=row["result"],
            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
            resolution=row.get("resolution"), reconciled_by=row.get("reconciled_by"),
            reconciliation_note=row.get("reconciliation_note"), reconciled_at=row.get("reconciled_at"),
        )


class ToolEffectReconciliationService:
    """Operator-gated workflow for resolving ambiguous external side effects."""

    def __init__(
        self, *, store: ToolEffectStore,
        authorize_operator: Callable[[str, str, str, str], Any],
        audit_sink: Any,
    ) -> None:
        self.store = store
        self.authorize_operator = authorize_operator
        self.audit_sink = audit_sink

    async def reconcile(
        self, *, tenant_id: str, run_id: str, call_id: str,
        operator_id: str, resolution: str, note: str, result: Any = None,
    ) -> ToolEffect:
        if not all(value.strip() for value in (tenant_id, run_id, call_id, operator_id)):
            raise ValueError("tenant, Run, call and operator identifiers are required")
        if not isinstance(note, str) or not note.strip() or len(note) > 1000:
            raise ValueError("a 1-1000 character reconciliation note is required")
        allowed = self.authorize_operator(operator_id, tenant_id, run_id, call_id)
        if hasattr(allowed, "__await__"):
            allowed = await allowed
        if not allowed:
            raise PermissionError("operator is not authorized to reconcile this tool effect")
        current = await self.store.get(
            tenant_id=tenant_id, run_id=run_id, call_id=call_id,
        )
        if current is None:
            raise KeyError("tool effect not found")
        await self.audit_sink.record(
            tenant_id=tenant_id, actor_id=operator_id,
            action="tool_effect.reconcile", resource=f"{run_id}/{call_id}",
            action_digest=current.action_digest, outcome="reconciliation_started",
            run_id=run_id, detail=resolution,
        )
        effect = await self.store.reconcile(
            tenant_id=tenant_id, run_id=run_id, call_id=call_id,
            operator_id=operator_id, resolution=resolution, note=note, result=result,
        )
        await self.audit_sink.record(
            tenant_id=tenant_id, actor_id=operator_id,
            action="tool_effect.reconcile", resource=f"{run_id}/{call_id}",
            action_digest=effect.action_digest, outcome="reconciliation_completed",
            run_id=run_id, detail=f"{resolution}: {note}",
        )
        return effect
