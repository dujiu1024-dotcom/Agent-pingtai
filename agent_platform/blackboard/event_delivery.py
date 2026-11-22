"""Leased, idempotent delivery of durable Blackboard events.

The outbox is at-least-once. Handlers MUST use event_id as their downstream
idempotency key; this adapter provides ownership fencing and durable retries.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

ConnectionFactory = Callable[[], Any]


@dataclass(frozen=True)
class BlackboardDelivery:
    tenant_id: str
    event_id: str
    event_type: str
    scope: str
    scope_id: str
    entry_id: str
    entry_version: int
    actor_id: str
    payload: dict[str, Any]
    correlation_id: str
    occurred_at: datetime
    lease_token: str
    attempt: int


class BlackboardEventDeliveryStore:
    """PostgreSQL lease queue with retry, fencing and manual redrive."""

    def __init__(
        self, *, tenant_id: str, connection_factory: ConnectionFactory,
        lease_seconds: int = 60, max_attempts: int = 8,
        retry_base_seconds: int = 2, retry_max_seconds: int = 300,
    ) -> None:
        if not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 1 and 3600")
        if not 1 <= max_attempts <= 100:
            raise ValueError("max_attempts must be between 1 and 100")
        if retry_base_seconds < 1 or retry_max_seconds < retry_base_seconds:
            raise ValueError("retry bounds are invalid")
        self.tenant_id = tenant_id
        self._connection_factory = connection_factory
        self.lease_seconds = lease_seconds
        self.max_attempts = max_attempts
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds

    async def claim_next(self, *, worker_id: str) -> BlackboardDelivery | None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        token = uuid4()
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    UPDATE blackboard_event_deliveries
                    SET status = 'manual', worker_id = NULL, lease_token = NULL,
                        lease_expires_at = NULL,
                        last_error = COALESCE(last_error, 'worker lease expired at retry limit'),
                        updated_at = now()
                    WHERE tenant_id = %s AND status = 'processing'
                      AND lease_expires_at <= now() AND attempts >= %s
                    """,
                    (self.tenant_id, self.max_attempts),
                )
                cursor = await connection.execute(
                    """
                    WITH candidate AS (
                        SELECT tenant_id, event_id
                        FROM blackboard_event_deliveries
                        WHERE tenant_id = %s
                          AND (
                            (status = 'pending' AND next_attempt_at <= now())
                            OR (status = 'processing' AND lease_expires_at <= now() AND attempts < %s)
                          )
                        ORDER BY next_attempt_at, event_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    UPDATE blackboard_event_deliveries AS delivery
                    SET status = 'processing',
                        worker_id = %s,
                        lease_token = %s,
                        lease_expires_at = now() + (%s * interval '1 second'),
                        attempts = attempts + 1,
                        updated_at = now()
                    FROM candidate
                    JOIN blackboard_events AS event
                      ON event.tenant_id = candidate.tenant_id
                     AND event.event_id = candidate.event_id
                    WHERE delivery.tenant_id = candidate.tenant_id
                      AND delivery.event_id = candidate.event_id
                    RETURNING delivery.attempts, delivery.lease_token,
                      event.tenant_id, event.event_id, event.event_type,
                      event.scope, event.scope_id, event.entry_id,
                      event.entry_version, event.actor_id, event.payload,
                      event.correlation_id, event.occurred_at
                    """,
                    (self.tenant_id, self.max_attempts, worker_id, token, self.lease_seconds),
                )
                row = await cursor.fetchone()
        if row is None:
            return None
        return BlackboardDelivery(
            tenant_id=str(row["tenant_id"]), event_id=str(row["event_id"]),
            event_type=row["event_type"], scope=row["scope"],
            scope_id=row["scope_id"], entry_id=str(row["entry_id"]),
            entry_version=row["entry_version"], actor_id=row["actor_id"],
            payload=dict(row["payload"] or {}),
            correlation_id=str(row["correlation_id"]),
            occurred_at=row["occurred_at"],
            lease_token=str(row["lease_token"]), attempt=row["attempts"],
        )

    async def complete(self, *, event_id: str, lease_token: str) -> None:
        await self._finish(
            event_id=event_id, lease_token=lease_token, status="completed",
            error=None,
        )

    async def fail(
        self, *, event_id: str, lease_token: str, error: str,
    ) -> str:
        if not isinstance(error, str) or not error.strip():
            raise ValueError("error must not be empty")
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    SELECT attempts FROM blackboard_event_deliveries
                    WHERE tenant_id = %s AND event_id = %s
                      AND status = 'processing' AND lease_token = %s
                      AND lease_expires_at > now()
                    FOR UPDATE
                    """,
                    (self.tenant_id, UUID(event_id), UUID(lease_token)),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise RuntimeError("delivery lease lost or expired")
                attempts = int(row["attempts"])
                if attempts >= self.max_attempts:
                    status, delay = "manual", None
                else:
                    status = "pending"
                    delay = min(
                        self.retry_base_seconds * (2 ** (attempts - 1)),
                        self.retry_max_seconds,
                    )
                await connection.execute(
                    """
                    UPDATE blackboard_event_deliveries
                    SET status = %s, worker_id = NULL, lease_token = NULL,
                        lease_expires_at = NULL, last_error = %s,
                        next_attempt_at = COALESCE(now() + (%s * interval '1 second'), now()),
                        updated_at = now()
                    WHERE tenant_id = %s AND event_id = %s
                    """,
                    (status, error[:2000], delay, self.tenant_id, UUID(event_id)),
                )
        return status

    async def _finish(
        self, *, event_id: str, lease_token: str, status: str,
        error: str | None,
    ) -> None:
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """
                UPDATE blackboard_event_deliveries
                SET status = %s, worker_id = NULL, lease_token = NULL,
                    lease_expires_at = NULL, last_error = %s,
                    completed_at = CASE WHEN %s = 'completed' THEN now() ELSE completed_at END,
                    updated_at = now()
                WHERE tenant_id = %s AND event_id = %s
                  AND status = 'processing' AND lease_token = %s
                  AND lease_expires_at > now()
                RETURNING event_id
                """,
                (status, error, status, self.tenant_id, UUID(event_id), UUID(lease_token)),
            )
            if await cursor.fetchone() is None:
                raise RuntimeError("delivery lease lost or expired")

    async def redrive(
        self, *, event_id: str, actor_id: str, reason: str,
    ) -> None:
        """Explicitly requeue a dead-lettered event with an audit record."""
        if not actor_id.strip() or not reason.strip():
            raise ValueError("manual redrive requires actor_id and reason")
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE blackboard_event_deliveries
                    SET status = 'pending', attempts = 0, last_error = NULL,
                        next_attempt_at = now(), updated_at = now()
                    WHERE tenant_id = %s AND event_id = %s AND status = 'manual'
                    RETURNING event_id
                    """,
                    (self.tenant_id, UUID(event_id)),
                )
                if await cursor.fetchone() is None:
                    raise RuntimeError("only manual deliveries can be redriven")
                await connection.execute(
                    """
                    INSERT INTO blackboard_event_delivery_interventions
                      (tenant_id, event_id, actor_id, reason, action)
                    VALUES (%s, %s, %s, %s, 'redrive')
                    """,
                    (self.tenant_id, UUID(event_id), actor_id, reason[:1000]),
                )

    @staticmethod
    def retry_delay(attempt: int, *, base_seconds: int = 2, max_seconds: int = 300) -> timedelta:
        if attempt < 1 or base_seconds < 1 or max_seconds < base_seconds:
            raise ValueError("invalid retry parameters")
        return timedelta(seconds=min(base_seconds * (2 ** (attempt - 1)), max_seconds))
