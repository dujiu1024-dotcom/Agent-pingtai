"""Durable alert history and acknowledgement lifecycle."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import uuid4

from agent_platform.observability.alerts import AlertEvent


class PostgresAlertEventStore:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def record(self, event: AlertEvent, *, scope_key: str, dedupe_key: str) -> str:
        if not scope_key.strip() or not dedupe_key.strip():
            raise ValueError("scope_key and dedupe_key are required")
        event_id = str(uuid4())
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_alert_events
                   (event_id,scope_key,dedupe_key,rule_id,metric_name,observed_value,
                    threshold,severity,fired_at,description,status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'open')
                   ON CONFLICT (scope_key,dedupe_key) DO NOTHING
                   RETURNING event_id""",
                (event_id,scope_key,dedupe_key,event.rule_id,event.metric_name,
                 event.observed_value,event.threshold,event.severity,event.fired_at,
                 event.description),
            )
            row = await cursor.fetchone()
            if row is not None:
                return str(row["event_id"])
            cursor = await connection.execute(
                "SELECT event_id FROM platform_alert_events WHERE scope_key=%s AND dedupe_key=%s",
                (scope_key,dedupe_key),
            )
            existing = await cursor.fetchone()
        if existing is None:
            raise RuntimeError("alert deduplication record disappeared")
        return str(existing["event_id"])

    async def list_open(self, *, scope_key: str, limit: int = 100) -> list[dict[str, Any]]:
        if not scope_key.strip() or not 1 <= limit <= 500:
            raise ValueError("scope_key is required and limit must be between 1 and 500")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT event_id,scope_key,rule_id,metric_name,observed_value,
                          threshold,severity,fired_at,description,status,
                          acknowledged_by,acknowledged_at,resolved_at
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
