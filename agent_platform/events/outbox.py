"""At-least-once PostgreSQL Outbox to Redis Streams relay."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
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
                          AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                          AND attempts < max_attempts
                          AND dead_lettered_at IS NULL
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

    async def requeue_published(
        self,
        *,
        since: datetime,
        until: datetime,
        confirmation: str,
        tenant_id: str | None = None,
        limit: int = 1000,
    ) -> int:
        """Requeue a bounded published window after Redis data loss.

        Consumers must retain durable event receipts and deduplicate by event_id.
        The explicit confirmation and mandatory time window prevent accidental
        unbounded replay of the entire outbox.
        """
        if confirmation != "REPLAY_PUBLISHED_OUTBOX":
            raise ValueError("explicit outbox replay confirmation is required")
        for name, value in (("since", since), ("until", until)):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be a timezone-aware datetime")
        if since >= until:
            raise ValueError("since must be earlier than until")
        if tenant_id is not None and (not isinstance(tenant_id, str) or not tenant_id.strip()):
            raise ValueError("tenant_id must be a non-empty string when supplied")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError("limit must be an integer between 1 and 10000")

        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    WITH candidates AS (
                        SELECT tenant_id, outbox_id
                        FROM platform_outbox
                        WHERE published_at IS NOT NULL
                          AND published_at >= %s AND published_at < %s
                          AND (%s IS NULL OR tenant_id = %s)
                        ORDER BY published_at, tenant_id, outbox_id
                        LIMIT %s
                        FOR UPDATE SKIP LOCKED
                    )
                    UPDATE platform_outbox AS outbox
                    SET published_at = NULL, lease_token = NULL, lease_until = NULL,
                        last_error = 'recovery replay requested', next_attempt_at = NULL
                    FROM candidates
                    WHERE outbox.tenant_id = candidates.tenant_id
                      AND outbox.outbox_id = candidates.outbox_id
                    RETURNING outbox.outbox_id
                    """,
                    (since, until, tenant_id, tenant_id, limit),
                )
                return len(await cursor.fetchall())

    async def mark_published(self, record: OutboxRecord) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE platform_outbox
                    SET published_at = now(), lease_token = NULL, lease_until = NULL,
                        last_error = NULL, next_attempt_at = NULL, dead_lettered_at = NULL
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
                    SET lease_token = NULL, lease_until = NULL, last_error = %s,
                        dead_lettered_at = CASE WHEN attempts >= max_attempts THEN now() ELSE NULL END,
                        next_attempt_at = CASE WHEN attempts >= max_attempts THEN NULL
                          ELSE now() + make_interval(
                            secs => LEAST(300, power(2, LEAST(attempts, 8)))::int
                          ) END
                    WHERE tenant_id = %s AND outbox_id = %s AND lease_token = %s
                    """,
                    (
                        error[:1000], record.tenant_id, record.outbox_id,
                        UUID(record.lease_token),
                    ),
                )


class OutboxRelay:
    SYSTEM_ACTOR_ID = "system:outbox-relay"

    def __init__(
        self, *, store: OutboxStore, transport: EventTransport, audit_sink: Any,
    ) -> None:
        if not callable(getattr(audit_sink, "record", None)):
            raise TypeError("outbox relay requires an audit sink with record()")
        self.store = store
        self.transport = transport
        self.audit_sink = audit_sink

    async def publish_pending(self, *, limit: int = 50) -> dict[str, int]:
        records = await self.store.claim_batch(limit=limit)
        result = {"claimed": len(records), "published": 0, "failed": 0}
        for record in records:
            detail = {
                "outbox_id": record.outbox_id,
                "event_id": record.event_id,
                "topic": record.topic,
            }
            # Persist intent before publishing to Redis. If audit storage is
            # unavailable, release the lease for retry and do not publish.
            try:
                await self.audit_sink.record(
                    tenant_id=record.tenant_id,
                    actor_id=self.SYSTEM_ACTOR_ID,
                    action="outbox.event.publish.attempt",
                    subject_id=record.event_id,
                    outcome="succeeded",
                    detail=detail,
                )
            except Exception:
                await self.store.mark_failed(record, "AuditUnavailable")
                result["failed"] += 1
                continue

            try:
                stream_id = await self.transport.publish(
                    tenant_id=record.tenant_id,
                    topic=record.topic,
                    event_id=record.event_id,
                    payload=record.payload,
                )
            except Exception as exc:
                await self.store.mark_failed(record, type(exc).__name__)
                await self.audit_sink.record(
                    tenant_id=record.tenant_id,
                    actor_id=self.SYSTEM_ACTOR_ID,
                    action="outbox.event.publish",
                    subject_id=record.event_id,
                    outcome="failed",
                    detail={**detail, "error_type": type(exc).__name__},
                )
                result["failed"] += 1
                continue

            # Publishing succeeded even if the lease was lost before the
            # PostgreSQL acknowledgement. Record the external side effect and
            # its persistence outcome separately; consumers dedupe by event_id.
            try:
                marked_published = await self.store.mark_published(record)
                mark_error_type = None
            except Exception as exc:
                marked_published = False
                mark_error_type = type(exc).__name__
            await self.audit_sink.record(
                tenant_id=record.tenant_id,
                actor_id=self.SYSTEM_ACTOR_ID,
                action="outbox.event.publish",
                subject_id=record.event_id,
                outcome="succeeded",
                detail={
                    **detail,
                    "stream_id": stream_id,
                    "lease_marked_published": marked_published,
                    "mark_error_type": mark_error_type,
                },
            )
            if marked_published:
                result["published"] += 1
            else:
                # The lease was lost or DB acknowledgement failed after
                # publish. A duplicate is possible; consumers dedupe event_id.
                result["failed"] += 1
        return result
