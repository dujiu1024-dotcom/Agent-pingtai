"""PostgreSQL implementation of the Hybrid Blackboard repository port.

The tenant ID is bound at construction and is injected into every query.
PostgreSQL stores durable facts; event publication is handled by the outbox.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Sequence
from uuid import UUID

from agent_platform.blackboard.protocol import BlackboardRepository, VersionConflict
from agent_platform.contracts import (
    BlackboardEntry,
    BlackboardEvent,
    EntryKind,
    EntryStatus,
    Scope,
    utc_now,
)

ConnectionFactory = Callable[[], Any]


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
    return Jsonb(value)


class PostgresBlackboardRepository(BlackboardRepository):
    def __init__(self, *, tenant_id: str, connection_factory: ConnectionFactory) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.tenant_id = tenant_id
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, *, tenant_id: str, dsn: str) -> "PostgresBlackboardRepository":
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")

        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "Install psycopg[binary,pool] for PostgreSQL storage"
                ) from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)

        return cls(tenant_id=tenant_id, connection_factory=connect)

    async def _persist_event(self, connection: Any, event: BlackboardEvent) -> None:
        cursor = await connection.execute(
            """
            INSERT INTO blackboard_events (
                tenant_id, event_id, event_type, scope, scope_id,
                entry_id, entry_version, actor_id, payload, correlation_id, occurred_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
            RETURNING event_id
            """,
            (
                self.tenant_id, UUID(event.event_id), event.event_type, event.scope.value,
                event.scope_id, UUID(event.entry_id), event.entry_version, event.actor_id,
                _jsonb(event.payload), UUID(event.correlation_id), event.occurred_at,
            ),
        )
        inserted = await cursor.fetchone()
        if inserted is None:
            existing_cursor = await connection.execute(
                """
                SELECT event_type, scope, scope_id, entry_id, entry_version,
                       actor_id, payload, correlation_id, occurred_at
                FROM blackboard_events
                WHERE tenant_id = %s AND event_id = %s
                """,
                (self.tenant_id, UUID(event.event_id)),
            )
            existing = await existing_cursor.fetchone()
            if existing is None:
                raise RuntimeError("Blackboard event conflict has no matching durable event")
            expected = (
                event.event_type, event.scope.value, event.scope_id, UUID(event.entry_id),
                event.entry_version, event.actor_id, event.payload, UUID(event.correlation_id),
                event.occurred_at,
            )
            actual = (
                existing["event_type"], existing["scope"], existing["scope_id"],
                existing["entry_id"], existing["entry_version"], existing["actor_id"],
                existing["payload"], existing["correlation_id"], existing["occurred_at"],
            )
            if actual != expected:
                raise ValueError("Blackboard event_id was reused with a different payload")
            return
        await connection.execute(
            """
            INSERT INTO blackboard_event_deliveries (tenant_id, event_id)
            VALUES (%s, %s)
            ON CONFLICT (tenant_id, event_id) DO NOTHING
            """,
            (self.tenant_id, UUID(event.event_id)),
        )
        await connection.execute(
            """
            INSERT INTO platform_outbox (tenant_id, event_id, topic, payload)
            VALUES (%s, %s, %s, %s)
            """,
            (
                self.tenant_id,
                UUID(event.event_id),
                event.event_type,
                _jsonb({
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "scope": event.scope.value,
                    "scope_id": event.scope_id,
                    "entry_id": event.entry_id,
                    "entry_version": event.entry_version,
                    "actor_id": event.actor_id,
                    "payload": event.payload,
                    "correlation_id": event.correlation_id,
                    "occurred_at": event.occurred_at.isoformat(),
                }),
            ),
        )

    async def propose(self, entry: BlackboardEntry) -> BlackboardEntry:
        event = BlackboardEvent(
            event_type="blackboard.entry_proposed",
            scope=entry.scope,
            scope_id=entry.scope_id,
            entry_id=entry.entry_id,
            entry_version=entry.version,
            actor_id=entry.author_id,
            payload={"kind": entry.kind.value, "status": entry.status.value},
            correlation_id=entry.correlation_id,
        )
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO blackboard_entries (
                        tenant_id, entry_id, scope, scope_id, kind, version, status,
                        content, source_refs, author_id, correlation_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.tenant_id, UUID(entry.entry_id), entry.scope.value, entry.scope_id,
                        entry.kind.value, entry.version, entry.status.value,
                        _jsonb(entry.content), _jsonb(list(entry.source_refs)),
                        entry.author_id, UUID(entry.correlation_id), entry.created_at,
                    ),
                )
                await self._persist_event(connection, event)
        return entry

    @staticmethod
    def _to_entry(row: dict[str, Any]) -> BlackboardEntry:
        return BlackboardEntry(
            entry_id=str(row["entry_id"]),
            scope=Scope(row["scope"]),
            scope_id=row["scope_id"],
            kind=EntryKind(row["kind"]),
            content=dict(row["content"]),
            author_id=row["author_id"],
            source_refs=tuple(row["source_refs"] or ()),
            version=row["version"],
            status=EntryStatus(row["status"]),
            created_at=row["created_at"],
            correlation_id=str(row["correlation_id"]),
        )

    async def get(self, entry_id: str) -> BlackboardEntry | None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """
                SELECT * FROM blackboard_entries
                WHERE tenant_id = %s AND entry_id = %s
                ORDER BY version DESC LIMIT 1
                """,
                (self.tenant_id, UUID(entry_id)),
            )
            row = await cursor.fetchone()
        return self._to_entry(row) if row else None

    async def list_entries(
        self,
        *,
        scope: Scope,
        scope_id: str,
        kinds: Sequence[str] = (),
        limit: int = 100,
    ) -> list[BlackboardEntry]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        query = """
            SELECT * FROM (
                SELECT DISTINCT ON (entry_id) *
                FROM blackboard_entries
                WHERE tenant_id = %s AND scope = %s AND scope_id = %s
        """
        params: list[Any] = [self.tenant_id, scope.value, scope_id]
        if kinds:
            query += " AND kind = ANY(%s)"
            params.append(list(kinds))
        query += """
                ORDER BY entry_id, version DESC
            ) latest
            ORDER BY created_at DESC
            LIMIT %s
        """
        params.append(limit)
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [self._to_entry(row) for row in rows]

    async def list_all(
        self,
        *,
        scope: Scope | None = None,
        scope_id: str | None = None,
        kinds: Sequence[str] = (),
        limit: int = 100,
    ) -> list[BlackboardEntry]:
        if limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        query = """
            SELECT * FROM (
                SELECT DISTINCT ON (entry_id) *
                FROM blackboard_entries
                WHERE tenant_id = %s
        """
        params: list[Any] = [self.tenant_id]
        if scope is not None:
            query += " AND scope = %s"
            params.append(scope.value)
        if scope_id is not None and scope_id.strip():
            query += " AND scope_id = %s"
            params.append(scope_id.strip())
        if kinds:
            query += " AND kind = ANY(%s)"
            params.append(list(kinds))
        query += """
                ORDER BY entry_id, version DESC
            ) latest
            ORDER BY created_at DESC
            LIMIT %s
        """
        params.append(limit)
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(query, tuple(params))
            rows = await cursor.fetchall()
        return [self._to_entry(row) for row in rows]

    async def list_versions(self, entry_id: str) -> list[BlackboardEntry]:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """
                SELECT * FROM blackboard_entries
                WHERE tenant_id = %s AND entry_id = %s
                ORDER BY version ASC
                """,
                (self.tenant_id, UUID(entry_id)),
            )
            rows = await cursor.fetchall()
        return [self._to_entry(row) for row in rows]

    async def revise(
        self,
        entry_id: str,
        *,
        expected_version: int,
        replacement: BlackboardEntry,
    ) -> BlackboardEntry:
        if replacement.entry_id != entry_id:
            raise ValueError("replacement must retain the original entry_id")
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (f"{self.tenant_id}:{entry_id}",),
                )
                cursor = await connection.execute(
                    """
                    SELECT version, scope, scope_id
                    FROM blackboard_entries
                    WHERE tenant_id = %s AND entry_id = %s
                    ORDER BY version DESC LIMIT 1 FOR UPDATE
                    """,
                    (self.tenant_id, UUID(entry_id)),
                )
                current = await cursor.fetchone()
                if current is None:
                    raise KeyError(entry_id)
                if current["version"] != expected_version:
                    raise VersionConflict(
                        f"expected version {expected_version}, found {current['version']}"
                    )
                if (
                    replacement.scope.value != current["scope"]
                    or replacement.scope_id != current["scope_id"]
                ):
                    raise ValueError("an entry revision cannot change its scope")
                next_entry = replace(
                    replacement,
                    version=expected_version + 1,
                    created_at=utc_now(),
                )
                await connection.execute(
                    """
                    INSERT INTO blackboard_entries (
                        tenant_id, entry_id, scope, scope_id, kind, version, status,
                        content, source_refs, author_id, correlation_id, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        self.tenant_id, UUID(next_entry.entry_id), next_entry.scope.value,
                        next_entry.scope_id, next_entry.kind.value, next_entry.version,
                        next_entry.status.value, _jsonb(next_entry.content),
                        _jsonb(list(next_entry.source_refs)), next_entry.author_id,
                        UUID(next_entry.correlation_id), next_entry.created_at,
                    ),
                )
                await self._persist_event(
                    connection,
                    BlackboardEvent(
                        event_type="blackboard.entry_revised",
                        scope=next_entry.scope,
                        scope_id=next_entry.scope_id,
                        entry_id=next_entry.entry_id,
                        entry_version=next_entry.version,
                        actor_id=next_entry.author_id,
                        payload={"previous_version": expected_version},
                        correlation_id=next_entry.correlation_id,
                    ),
                )
        return next_entry

    async def append_event(self, event: BlackboardEvent) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await self._persist_event(connection, event)

    async def record_challenge(
        self, entry_id: str, challenger_id: str, reason: str,
        evidence_refs: Sequence[str]
    ) -> BlackboardEntry:
        target = await self.get(entry_id)
        if target is None:
            raise KeyError(entry_id)
        challenge = BlackboardEntry(
            scope=target.scope,
            scope_id=target.scope_id,
            kind=EntryKind.CHALLENGE,
            content={
                "target_entry_id": target.entry_id,
                "target_version": target.version,
                "reason": reason,
                "evidence_refs": list(evidence_refs),
            },
            author_id=challenger_id,
            source_refs=tuple(evidence_refs),
            correlation_id=target.correlation_id,
        )
        return await self.propose(challenge)

    async def record_decision(
        self, conflict_id: str, decider_id: str, outcome: str, rationale: str,
        evidence_refs: Sequence[str]
    ) -> BlackboardEntry:
        conflict = await self.get(conflict_id)
        if conflict is None:
            raise KeyError(conflict_id)
        if conflict.kind != EntryKind.CONFLICT:
            raise ValueError("record_decision requires a conflict entry")
        decision = BlackboardEntry(
            scope=conflict.scope,
            scope_id=conflict.scope_id,
            kind=EntryKind.DECISION,
            content={
                "conflict_id": conflict.entry_id,
                "outcome": outcome,
                "rationale": rationale,
                "evidence_refs": list(evidence_refs),
            },
            author_id=decider_id,
            source_refs=tuple(evidence_refs),
            status=EntryStatus.ACCEPTED,
            correlation_id=conflict.correlation_id,
        )
        return await self.propose(decision)
