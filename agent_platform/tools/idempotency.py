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


class ToolEffectStore(Protocol):
    async def get(self, *, tenant_id: str, run_id: str, call_id: str) -> ToolEffect | None: ...
    async def claim(
        self, *, tenant_id: str, run_id: str, call_id: str,
        action_digest: str, actor_id: str,
    ) -> ToolEffect: ...
    async def complete(self, *, tenant_id: str, run_id: str, call_id: str, result: Any) -> None: ...
    async def fail(self, *, tenant_id: str, run_id: str, call_id: str, error: str) -> None: ...


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
            return copy.deepcopy(old)
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


class PostgresToolEffectStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def get(self, *, tenant_id: str, run_id: str, call_id: str) -> ToolEffect | None:
        db = await self._connect()
        async with db:
            cur = await db.execute(
                """SELECT tenant_id,run_id,call_id,action_digest,actor_id,state,
                          result,error,created_at,updated_at
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
                              result,error,created_at,updated_at
                       FROM platform_tool_effects
                       WHERE tenant_id=%s AND run_id=%s AND call_id=%s FOR UPDATE""",
                    (tenant_id, run_id, call_id),
                )
                row = await cur.fetchone()
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

    @staticmethod
    def _to_effect(row: Mapping[str, Any]) -> ToolEffect:
        return ToolEffect(
            tenant_id=row["tenant_id"], run_id=str(row["run_id"]),
            call_id=row["call_id"], action_digest=row["action_digest"],
            actor_id=row["actor_id"], state=row["state"], result=row["result"],
            error=row["error"], created_at=row["created_at"], updated_at=row["updated_at"],
        )
