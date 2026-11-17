"""SSE bridge for the durable Blackboard event history.

PostgreSQL Blackboard events are committed with their outbox records. This
bridge tails that same tenant-scoped event log, so subscribers can reconnect
without introducing a second in-memory event bus.
"""
from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any
from uuid import UUID

from agent_platform.blackboard.replay import BlackboardReplayCursor
from agent_platform.contracts import Scope
from agent_platform.streaming.sse import SSEEvent


def encode_blackboard_cursor(cursor: BlackboardReplayCursor) -> str:
    timestamp = cursor.occurred_at.isoformat()
    return f"{timestamp}|{cursor.event_id}"


def decode_blackboard_cursor(value: str | None) -> BlackboardReplayCursor | None:
    if value is None:
        return None
    timestamp, separator, event_id = value.partition("|")
    if not separator or not timestamp or not event_id:
        raise ValueError("Last-Event-ID must contain timestamp and event UUID")
    try:
        occurred_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        UUID(event_id)
        return BlackboardReplayCursor(occurred_at=occurred_at, event_id=event_id)
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("Last-Event-ID is not a valid Blackboard cursor") from exc


async def stream_blackboard_events(
    *,
    replay: Any,
    scope: Scope,
    scope_id: str,
    cursor: BlackboardReplayCursor | None = None,
    poll_interval: float = 0.5,
    batch_size: int = 100,
):
    """Yield durable Blackboard changes after the cursor, polling until disconnected."""
    if not scope_id.strip():
        raise ValueError("scope_id must not be empty")
    if poll_interval <= 0 or not 1 <= batch_size <= 1000:
        raise ValueError("poll interval and batch size must be positive and bounded")
    current = cursor
    if current is None:
        current = await replay.latest_cursor(scope=scope, scope_id=scope_id)
    while True:
        events = await replay.list_after(
            cursor=current, scope=scope, scope_id=scope_id, limit=batch_size,
        )
        if not events:
            await asyncio.sleep(poll_interval)
            continue
        for event in events:
            current = replay.cursor_for(event)
            yield SSEEvent(
                event="blackboard_update",
                event_id=encode_blackboard_cursor(current),
                data={
                    "event_id": event.event_id,
                    "event_type": event.event_type,
                    "scope": event.scope.value,
                    "scope_id": event.scope_id,
                    "entry_id": event.entry_id,
                    "entry_version": event.entry_version,
                    "actor_id": event.actor_id,
                    "correlation_id": event.correlation_id,
                    "occurred_at": event.occurred_at.isoformat(),
                },
            )
