"""Tenant-scoped, cursor-based replay for durable Blackboard events.

Replay is read-only: consumers can resume from a stable (occurred_at, event_id)
cursor without mutating the outbox or re-emitting external side effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from agent_platform.contracts import BlackboardEvent, Scope

ConnectionFactory = Callable[[], Any]


@dataclass(frozen=True)
class BlackboardReplayCursor:
    occurred_at: datetime
    event_id: str

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("replay cursor timestamp must be timezone-aware")
        try:
            UUID(self.event_id)
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("replay cursor event_id must be a UUID") from exc


class PostgresBlackboardEventReplay:
    """Read durable event history for one tenant in deterministic cursor order."""

    def __init__(
        self, *, tenant_id: str, connection_factory: ConnectionFactory
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.tenant_id = tenant_id
        self._connection_factory = connection_factory

    async def latest_cursor(
        self, *, scope: Scope, scope_id: str,
    ) -> BlackboardReplayCursor | None:
        """Return the newest durable event cursor for a new live subscription."""
        if not scope_id.strip():
            raise ValueError("scope_id must not be empty")
        connection = await self._connection_factory()
        async with connection:
            result = await connection.execute(
                """
                SELECT event_id, occurred_at
                FROM blackboard_events
                WHERE tenant_id = %s AND scope = %s AND scope_id = %s
                ORDER BY occurred_at DESC, event_id DESC
                LIMIT 1
                """,
                (self.tenant_id, scope.value, scope_id),
            )
            row = await result.fetchone()
        if row is None:
            return None
        return BlackboardReplayCursor(
            occurred_at=row["occurred_at"], event_id=str(row["event_id"]),
        )

    async def list_after(
        self,
        *,
        cursor: BlackboardReplayCursor | None = None,
        scope: Scope | None = None,
        scope_id: str | None = None,
        limit: int = 100,
    ) -> list[BlackboardEvent]:
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        if (scope is None) != (scope_id is None):
            raise ValueError("scope and scope_id must be provided together")
        if scope_id is not None and not scope_id.strip():
            raise ValueError("scope_id must not be empty")

        query = """
            SELECT event_id, event_type, scope, scope_id, entry_id,
                   entry_version, actor_id, payload, correlation_id, occurred_at
            FROM blackboard_events
            WHERE tenant_id = %s
        """
        params: list[Any] = [self.tenant_id]
        if cursor is not None:
            query += " AND (occurred_at, event_id) > (%s, %s)"
            params.extend((cursor.occurred_at, UUID(cursor.event_id)))
        if scope is not None:
            query += " AND scope = %s AND scope_id = %s"
            params.extend((scope.value, scope_id))
        query += " ORDER BY occurred_at ASC, event_id ASC LIMIT %s"
        params.append(limit)

        connection = await self._connection_factory()
        async with connection:
            result = await connection.execute(query, tuple(params))
            rows = await result.fetchall()
        return [
            BlackboardEvent(
                event_id=str(row["event_id"]),
                event_type=row["event_type"],
                scope=Scope(row["scope"]),
                scope_id=row["scope_id"],
                entry_id=str(row["entry_id"]),
                entry_version=row["entry_version"],
                actor_id=row["actor_id"],
                payload=dict(row["payload"] or {}),
                correlation_id=str(row["correlation_id"]),
                occurred_at=row["occurred_at"],
            )
            for row in rows
        ]

    @staticmethod
    def cursor_for(event: BlackboardEvent) -> BlackboardReplayCursor:
        return BlackboardReplayCursor(
            occurred_at=event.occurred_at, event_id=event.event_id
        )
