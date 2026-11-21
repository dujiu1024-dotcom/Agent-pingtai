"""Durable alert history, notification outbox and acknowledgement lifecycle."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from agent_platform.observability.alerts import AlertEvent


class PostgresAlertEventStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def record_once(
        self, event: AlertEvent, *, scope_key: str, dedupe_key: str,
        on_call_user_id: str | None = None, ack_due_at: datetime | None = None,
    ) -> tuple[str, bool]:
        if not scope_key.strip() or not dedupe_key.strip():
            raise ValueError("scope_key and dedupe_key are required")
        if on_call_user_id is not None and (not isinstance(on_call_user_id, str) or not on_call_user_id.strip()):
            raise ValueError("on_call_user_id must be a non-empty string")
        if ack_due_at is not None and (ack_due_at.tzinfo is None or ack_due_at.utcoffset() is None):
            raise ValueError("ack_due_at must be timezone-aware")
        event_id = str(uuid4())
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_alert_events
                   (event_id,scope_key,dedupe_key,rule_id,metric_name,observed_value,
                    threshold,severity,fired_at,description,status,on_call_user_id,ack_due_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open',%s,%s)
                   ON CONFLICT (scope_key,dedupe_key) DO NOTHING
                   RETURNING event_id""",
                (event_id,scope_key,dedupe_key,event.rule_id,event.metric_name,
                 event.observed_value,event.threshold,event.severity,event.fired_at,
                 event.description,on_call_user_id,ack_due_at),
            )
            row = await cursor.fetchone()
            if row is not None:
                return str(row["event_id"]), True
            cursor = await connection.execute(
                """SELECT event_id FROM platform_alert_events
                   WHERE scope_key=%s AND dedupe_key=%s""",
                (scope_key,dedupe_key),
            )
            existing = await cursor.fetchone()
        if existing is None:
            raise RuntimeError("alert deduplication record disappeared")
        return str(existing["event_id"]), False

    async def record(self, event: AlertEvent, *, scope_key: str, dedupe_key: str) -> str:
        event_id, _ = await self.record_once(event, scope_key=scope_key, dedupe_key=dedupe_key)
        return event_id

    async def claim_pending_deliveries(self, *, limit: int = 100, event_id: str | None = None) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """WITH candidates AS (
                         SELECT event_id FROM platform_alert_events
                         WHERE ((delivery_status='pending' AND next_delivery_at <= now())
                              OR (delivery_status='failed' AND next_delivery_at <= now()
                                  AND delivery_attempts < delivery_max_attempts)
                              OR (delivery_status='sending' AND delivery_claimed_at < now() - interval '5 minutes'
                                  AND delivery_attempts < delivery_max_attempts))
                           AND (%s::uuid IS NULL OR event_id=%s)
                         ORDER BY fired_at
                         LIMIT %s
                         FOR UPDATE SKIP LOCKED
                       )
                       UPDATE platform_alert_events AS event
                       SET delivery_status='sending',delivery_attempts=delivery_attempts+1,delivery_claimed_at=now()
                       FROM candidates
                       WHERE event.event_id=candidates.event_id
                       RETURNING event.event_id,event.scope_key,event.rule_id,event.metric_name,
                                 event.observed_value,event.threshold,event.severity,
                                 event.fired_at,event.description,event.delivery_attempts,event.delivery_max_attempts""",
                    (event_id,event_id,limit),
                )
                rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_delivery(
        self, event_id: str, *, success: bool, now: datetime, error: str | None = None
    ) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """UPDATE platform_alert_events
                   SET delivery_status=CASE WHEN %s THEN 'sent'
                         WHEN delivery_attempts >= delivery_max_attempts THEN 'dead'
                         ELSE 'failed' END,
                       delivered_at=CASE WHEN %s THEN %s ELSE delivered_at END,
                       next_delivery_at=CASE WHEN %s OR delivery_attempts >= delivery_max_attempts THEN NULL
                         ELSE %s + (LEAST(3600,5 * power(2,GREATEST(delivery_attempts - 1,0))) * interval '1 second') END,
                       last_delivery_error=%s,delivery_claimed_at=NULL
                   WHERE event_id=%s AND delivery_status='sending'""",
                (success, success, now, success, now,
                 None if success else (error or "notification delivery failed")[:1000], event_id),
            )

    async def list_open(self, *, scope_key: str, limit: int = 100) -> list[dict[str, Any]]:
        if not scope_key.strip() or not 1 <= limit <= 500:
            raise ValueError("scope_key is required and limit must be between 1 and 500")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT event_id,scope_key,rule_id,metric_name,observed_value,
                          threshold,severity,fired_at,description,status,
                          acknowledged_by,acknowledged_at,resolved_at,on_call_user_id,ack_due_at,
                          delivery_status,delivery_attempts,delivery_max_attempts,next_delivery_at,
                          delivered_at,last_delivery_error
                   FROM platform_alert_events
                   WHERE scope_key=%s AND status IN ('open','acknowledged')
                   ORDER BY fired_at DESC LIMIT %s""",
                (scope_key,limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def acknowledge(self, event_id: str, *, scope_key: str, actor_id: str,
                          now: datetime) -> bool:
        if not scope_key.strip() or not actor_id.strip():
            raise ValueError("scope_key and actor_id are required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_alert_events
                   SET status='acknowledged',acknowledged_by=%s,acknowledged_at=%s
                   WHERE event_id=%s AND scope_key=%s AND status='open'
                   RETURNING event_id""",
                (actor_id,now,event_id,scope_key),
            )
            return await cursor.fetchone() is not None

    async def resolve(self, event_id: str, *, scope_key: str, actor_id: str,
                      now: datetime) -> bool:
        if not scope_key.strip() or not actor_id.strip():
            raise ValueError("scope_key and actor_id are required")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_alert_events
                   SET status='resolved',resolved_by=%s,resolved_at=%s
                   WHERE event_id=%s AND scope_key=%s
                     AND status IN ('open','acknowledged')
                   RETURNING event_id""",
                (actor_id,now,event_id,scope_key),
            )
            return await cursor.fetchone() is not None
