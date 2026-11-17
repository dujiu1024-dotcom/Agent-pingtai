"""Live Run event fan-out for the SSE transport.

The broker is intentionally an adapter boundary: production hosts must inject the
same shared broker/pub-sub implementation into API and worker processes.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Mapping
from typing import Any, cast

from agent_platform.streaming.sse import SSEEvent, SSEEventType

RUN_EVENT_NAMES = frozenset({
    "RUN_START", "STEP_START", "STEP_FINISH", "LLM_REQUEST", "LLM_RESPONSE",
    "TOOL_CALL", "TOOL_OUTPUT", "TOKEN_STREAM", "ERROR", "COMPLETE",
})


class RunEventBroker:
    """Tenant/run-scoped event fan-out with bounded replay for reconnecting clients."""

    def __init__(self, *, history_limit: int = 1000, subscriber_limit: int = 128) -> None:
        if history_limit < 1 or subscriber_limit < 1:
            raise ValueError("event broker limits must be positive")
        self.history_limit = history_limit
        self.subscriber_limit = subscriber_limit
        self._history: dict[tuple[str, str], deque[SSEEvent]] = defaultdict(
            lambda: deque(maxlen=history_limit)
        )
        self._subscribers: dict[tuple[str, str], set[asyncio.Queue[SSEEvent]]] = defaultdict(set)
        self._sequence: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    async def publish(
        self, *, tenant_id: str, run_id: str, event_type: str,
        data: Mapping[str, Any] | None = None,
    ) -> SSEEvent:
        if not tenant_id or not run_id or event_type not in RUN_EVENT_NAMES:
            raise ValueError("tenant, Run and supported Run event type are required")
        key = (tenant_id, run_id)
        async with self._lock:
            self._sequence[key] += 1
            sequence = self._sequence[key]
            payload = {"run_id": run_id, "sequence": sequence, "event_type": event_type}
            if data:
                payload.update(dict(data))
            event_name = (
                "token_stream" if event_type == "TOKEN_STREAM"
                else "tool_output" if event_type == "TOOL_OUTPUT"
                else "run_complete" if event_type == "COMPLETE"
                else "error" if event_type == "ERROR"
                else "step_progress"
            )
            event = SSEEvent(
                event=cast(SSEEventType, event_name),
                event_id=f"{run_id}:{sequence}",
                data=payload,
            )
            self._history[key].append(event)
            for queue in tuple(self._subscribers[key]):
                if queue.full():
                    # A slow subscriber is disconnected rather than silently losing
                    # ordered execution events; it can reconnect from Last-Event-ID.
                    self._subscribers[key].discard(queue)
                    continue
                queue.put_nowait(event)
            return event

    async def subscribe(
        self, *, tenant_id: str, run_id: str, last_event_id: str | None = None,
    ) -> AsyncIterator[SSEEvent]:
        if not tenant_id or not run_id:
            raise ValueError("tenant and Run are required")
        cursor = 0
        if last_event_id:
            prefix, separator, suffix = last_event_id.rpartition(":")
            if not separator or prefix != run_id or not suffix.isdigit():
                raise ValueError("Last-Event-ID does not belong to this Run")
            cursor = int(suffix)
        key = (tenant_id, run_id)
        queue: asyncio.Queue[SSEEvent] = asyncio.Queue(self.subscriber_limit)
        async with self._lock:
            history = tuple(
                event for event in self._history[key]
                if int((event.event_id or "").rpartition(":")[2]) > cursor
            )
            oldest = (
                int((self._history[key][0].event_id or "").rpartition(":")[2])
                if self._history[key] else self._sequence[key] + 1
            )
            if cursor and cursor < oldest - 1:
                raise ValueError("Last-Event-ID is older than the retained Run event window")
            if len(history) > self.subscriber_limit:
                raise ValueError("Run event backlog exceeds subscriber capacity")
            for event in history:
                queue.put_nowait(event)
            subscribers = self._subscribers[key]
            if len(subscribers) >= self.subscriber_limit:
                raise RuntimeError("Run event subscriber capacity reached")
            subscribers.add(queue)
        return self._iterate(key, queue)

    async def _iterate(
        self, key: tuple[str, str], queue: asyncio.Queue[SSEEvent],
    ) -> AsyncIterator[SSEEvent]:
        try:
            while True:
                event = await queue.get()
                yield event
                if event.data.get("event_type") in {"COMPLETE", "ERROR"}:
                    return
        finally:
            async with self._lock:
                self._subscribers[key].discard(queue)
