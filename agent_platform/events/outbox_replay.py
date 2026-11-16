"""Operator-controlled, tenant-scoped replay of published PostgreSQL outbox events."""
from __future__ import annotations

from typing import Any, Callable, Iterable
from uuid import UUID


class PostgresOutboxReplayStore:
    """Requeue a bounded explicit event set after Redis stream data loss.

    Event IDs remain unchanged so consumers can deduplicate at-least-once replay.
    This API never replays by wildcard or across tenants.
    """

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def requeue_published(
        self, *, tenant_id: str, event_ids: Iterable[str], actor_id: str,
        confirmation: str,
    ) -> tuple[str, ...]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not isinstance(actor_id, str) or not actor_id.strip():
            raise ValueError("actor_id is required")
        if confirmation != f"REPLAY-OUTBOX:{tenant_id}":
            raise PermissionError("explicit tenant replay confirmation is required")
        raw_ids = tuple(event_ids)
        if not raw_ids or len(raw_ids) > 500:
            raise ValueError("between 1 and 500 event IDs are required")
        if any(not isinstance(item, str) or not item.strip() for item in raw_ids):
            raise ValueError("event IDs must be non-empty strings")
        if len(raw_ids) != len(set(raw_ids)):
            raise ValueError("event IDs must be unique")
        try:
            parsed_ids = [UUID(item) for item in raw_ids]
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError("event IDs must be UUIDs") from exc

        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_outbox
                       SET published_at=NULL, lease_token=NULL, lease_until=NULL,
                           next_attempt_at=NULL, last_error='operator replay requested'
                       WHERE tenant_id=%s AND event_id=ANY(%s) AND published_at IS NOT NULL
                       RETURNING event_id""",
                    (tenant_id, parsed_ids),
                )
                rows = await cursor.fetchall()
        return tuple(str(row["event_id"]) for row in rows)
