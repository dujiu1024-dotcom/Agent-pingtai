"""PostgreSQL-backed idempotency receipts for at-least-once event consumers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
from uuid import UUID, uuid4

ConnectionFactory = Callable[[], Any]


@dataclass(frozen=True)
class EventReceiptClaim:
    tenant_id: str
    consumer_name: str
    event_id: str
    lease_token: str
    attempts: int


class PostgresEventReceiptStore:
    def __init__(self, connection_factory: ConnectionFactory, *, lease_seconds: int = 60) -> None:
        if lease_seconds < 5:
            raise ValueError("lease_seconds must be at least 5")
        self._connection_factory = connection_factory
        self.lease_seconds = lease_seconds

    async def claim(self, *, tenant_id: str, consumer_name: str, event_id: str) -> EventReceiptClaim | None:
        if not tenant_id or not consumer_name or not event_id:
            raise ValueError("tenant_id, consumer_name, and event_id are required")
        token = uuid4()
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    '''
                    INSERT INTO platform_event_receipts
                        (tenant_id, consumer_name, event_id, status, attempts, lease_token, lease_until)
                    VALUES (%s, %s, %s, 'processing', 1, %s, now() + make_interval(secs => %s))
                    ON CONFLICT (tenant_id, consumer_name, event_id) DO UPDATE
                    SET status = 'processing', attempts = platform_event_receipts.attempts + 1,
                        lease_token = EXCLUDED.lease_token, lease_until = EXCLUDED.lease_until,
                        last_error = NULL
                    WHERE platform_event_receipts.status = 'processing'
                      AND (platform_event_receipts.lease_until IS NULL OR platform_event_receipts.lease_until < now())
                    RETURNING attempts
                    ''',
                    (tenant_id, consumer_name, UUID(event_id), token, self.lease_seconds),
                )
                row = await cursor.fetchone()
        if row is None:
            return None
        return EventReceiptClaim(tenant_id, consumer_name, event_id, str(token), row['attempts'])

    async def is_completed(self, *, tenant_id: str, consumer_name: str, event_id: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                '''SELECT 1 FROM platform_event_receipts
                   WHERE tenant_id=%s AND consumer_name=%s AND event_id=%s AND status='completed' ''',
                (tenant_id, consumer_name, UUID(event_id)),
            )
            return await cursor.fetchone() is not None

    async def complete(self, claim: EventReceiptClaim) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    '''UPDATE platform_event_receipts
                       SET status='completed', completed_at=now(), lease_token=NULL,
                           lease_until=NULL, last_error=NULL
                       WHERE tenant_id=%s AND consumer_name=%s AND event_id=%s
                         AND status='processing' AND lease_token=%s
                       RETURNING event_id''',
                    (claim.tenant_id, claim.consumer_name, UUID(claim.event_id), UUID(claim.lease_token)),
                )
                return await cursor.fetchone() is not None

    async def fail(self, claim: EventReceiptClaim, error: str) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    '''UPDATE platform_event_receipts
                       SET lease_token=NULL, lease_until=NULL, last_error=%s
                       WHERE tenant_id=%s AND consumer_name=%s AND event_id=%s
                         AND status='processing' AND lease_token=%s
                       RETURNING event_id''',
                    (error[:1000], claim.tenant_id, claim.consumer_name, UUID(claim.event_id), UUID(claim.lease_token)),
                )
                return await cursor.fetchone() is not None
