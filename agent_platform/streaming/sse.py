"""SSE wire protocol shared by streaming API endpoints."""

from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

from sse_starlette.sse import EventSourceResponse, ServerSentEvent

SSEEventType = Literal[
    "token_stream",
    "step_progress",
    "tool_output",
    "run_complete",
    "run_error",
    "error",  # Backward-compatible alias; new producers should emit run_error.
    "blackboard_update",
]
SSE_EVENT_TYPES = frozenset({
    "token_stream",
    "step_progress",
    "tool_output",
    "run_complete",
    "run_error",
    "error",
    "blackboard_update",
})
DEFAULT_HEARTBEAT_INTERVAL = 15.0


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """A stable event envelope; event-specific fields live in the JSON data object."""

    event: SSEEventType
    data: Mapping[str, Any]
    event_id: str | None = None
    retry_ms: int | None = None

    def __post_init__(self) -> None:
        if self.event not in SSE_EVENT_TYPES:
            raise ValueError(f"unsupported SSE event type: {self.event!r}")
        if not isinstance(self.data, Mapping):
            raise TypeError("SSE event data must be a mapping")
        if self.event_id is not None and (
            not self.event_id or any(char in self.event_id for char in "\\r\\n\\0")
        ):
            raise ValueError("SSE event id must be non-empty and single-line")
        if self.retry_ms is not None and (
            isinstance(self.retry_ms, bool)
            or not isinstance(self.retry_ms, int)
            or self.retry_ms < 0
        ):
            raise ValueError("SSE retry_ms must be a non-negative integer")


def encode_sse(event: SSEEvent) -> bytes:
    """Encode one event using UTF-8 and the WHATWG Server-Sent Events framing."""
    payload = json.dumps(
        dict(event.data), ensure_ascii=False, separators=(",", ":"), allow_nan=False
    )
    lines: list[str] = []
    if event.event_id is not None:
        lines.append(f"id: {event.event_id}")
    if event.retry_ms is not None:
        lines.append(f"retry: {event.retry_ms}")
    lines.append(f"event: {event.event}")
    lines.extend(f"data: {line}" for line in payload.splitlines() or [""])
    return ("\\n".join(lines) + "\\n\\n").encode("utf-8")


HEARTBEAT_FRAME = b": heartbeat\\n\\n"


async def iter_sse(
    events: AsyncIterator[SSEEvent],
    *,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
) -> AsyncIterator[bytes]:
    """Yield encoded events and SSE comment heartbeats until the source completes.

    Kept as a small protocol iterator for deterministic tests and non-FastAPI
    transports. HTTP endpoints should use sse_response() and sse-starlette.
    """
    if (
        isinstance(heartbeat_interval, bool)
        or not math.isfinite(heartbeat_interval)
        or heartbeat_interval <= 0
    ):
        raise ValueError("heartbeat_interval must be a finite positive number")

    iterator = aiter(events)
    pending: asyncio.Task[SSEEvent] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(anext(iterator))
            done, _ = await asyncio.wait({pending}, timeout=heartbeat_interval)
            if not done:
                yield HEARTBEAT_FRAME
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                return
            finally:
                pending = None
            if not isinstance(event, SSEEvent):
                raise TypeError("SSE stream items must be SSEEvent instances")
            yield encode_sse(event)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            with suppress(asyncio.CancelledError):
                await pending
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


async def _event_source_messages(
    events: AsyncIterator[SSEEvent],
) -> AsyncIterator[dict[str, Any]]:
    async for event in events:
        if not isinstance(event, SSEEvent):
            raise TypeError("SSE stream items must be SSEEvent instances")
        payload = json.dumps(
            dict(event.data), ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        message: dict[str, Any] = {"event": event.event, "data": payload}
        if event.event_id is not None:
            message["id"] = event.event_id
        if event.retry_ms is not None:
            message["retry"] = event.retry_ms
        yield message


def sse_response(
    events: AsyncIterator[SSEEvent],
    *,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
) -> EventSourceResponse:
    """Build a standards-compliant SSE response using sse-starlette.

    sse-starlette owns ping frames, disconnect detection, and graceful response
    shutdown. Application event IDs remain available to clients via Last-Event-ID.
    """
    if (
        isinstance(heartbeat_interval, bool)
        or not math.isfinite(heartbeat_interval)
        or heartbeat_interval <= 0
    ):
        raise ValueError("heartbeat_interval must be a finite positive number")
    return EventSourceResponse(
        _event_source_messages(events),
        ping=heartbeat_interval,
        ping_message_factory=lambda: ServerSentEvent(comment="heartbeat"),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
