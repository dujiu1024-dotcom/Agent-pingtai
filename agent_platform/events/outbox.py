"""At-least-once PostgreSQL Outbox to Redis Streams relay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4


@dataclass(frozen=True)
class OutboxRecord:
    tenant_id: str
    outbox_id: int
    event_id: str
    topic: str
    payload: Mapping[str, Any]
    lease_token: str


class OutboxStore(Protocol):
    async def claim_batch(self, *, limit: int) -> list[OutboxRecord]:
        ...

    async def mark_published(self, record: OutboxRecord) -> bool:
        ...

    async def mark_failed(self, record: OutboxRecord, error: str) -> None:
        ...


class EventTransport(Protocol):
    async def publish(
        self,
        *,
        tenant_id: str,
        topic: str,
        event_id: str,
        payload: Mapping[str, Any],
    ) -> str:
        ...


class PostgresOutboxStore:
    def __init__(
        self,
        *,
        connection_factory: Any,
        lease_seconds: int = 60,
    ) -> None:
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least 5")
        self._connection_factory = connection_factory
        self.lease_seconds = lease_seconds

    async def claim_batch(self, *, limit: int = 50) -> list[OutboxRecord]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        lease_token = uuid4()
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    WITH candidates AS (
                        SELECT tenant_id, outbox_id
                        FROM platform_outbox
                        WHERE published_at IS NULL
                          AND (lease_until IS NULL OR lease_until < now())
                        ORDER BY created_at
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE platform_outbox AS outbox
                    SET lease_token = %s,
                        lease_until = now() + make_interval(secs => %s),
                        attempts = attempts + 1
                    FROM candidates
                    WHERE outbox.tenant_id = candidates.tenant_id
                      AND outbox.outbox_id = candidates.outbox_id
                    RETURNING outbox.tenant_id, outbox.outbox_id, outbox.event_id,
                              outbox.topic, outbox.payload, outbox.lease_token
                    """,
                    (limit, lease_token, self.lease_seconds),
                )
                rows = await cursor.fetchall()
        return [
            OutboxRecord(
                tenant_id=row["tenant_id"],
                outbox_id=row["outbox_id"],
                event_id=str(row["event_id"]),
                topic=row["topic"],
                payload=dict(row["payload"]),
                lease_token=str(row["lease_token"]),
            )
            for row in rows
        ]

    async def mark_published(self, record: OutboxRecord) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE platform_outbox
                    SET published_at = now(), lease_token = NULL, lease_until = NULL,
                        last_error = NULL
                    WHERE tenant_id = %s AND outbox_id = %s
                      AND lease_token = %s AND published_at IS NULL
                    RETURNING outbox_id
                    """,
                    (record.tenant_id, record.outbox_id, UUID(record.lease_token)),
                )
                return await cursor.fetchone() is not None

    async def mark_failed(self, record: OutboxRecord, error: str) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE platform_outbox
                    SET lease_token = NULL, lease_until = NULL, last_error = %s
                    WHERE tenant_id = %s AND outbox_id = %s AND lease_token = %s
                    """,
                    (
                        error[:1000], record.tenant_id, record.outbox_id,
                        UUID(record.lease_token),
                    ),
                )


class OutboxRelay:
    def __init__(self, *, store: OutboxStore, transport: EventTransport) -> None:
        self.store = store
        self.transport = transport

    async def publish_pending(self, *, limit: int = 50) -> dict[str, int]:
        records = await self.store.claim_batch(limit=limit)
        result = {"claimed": len(records), "published": 0, "failed": 0}
        for record in records:
            try:
                await self.transport.publish(
                    tenant_id=record.tenant_id,
                    topic=record.topic,
                    event_id=record.event_id,
                    payload=record.payload,
                )
                if await self.store.mark_published(record):
                    result["published"] += 1
                else:
                    # The lease was lost after publish. A duplicate is possible;
                    # downstream consumers must deduplicate on event_id.
                    result["failed"] += 1
            except Exception as exc:
                await self.store.mark_failed(record, type(exc).__name__)
                result["failed"] += 1
        return result
