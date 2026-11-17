"""SSE wire protocol shared by streaming API endpoints."""
from __future__ import annotations

import asyncio
import json
import math
from collections.abc import AsyncIterator, Mapping
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Literal

from fastapi.responses import StreamingResponse

SSEEventType = Literal["token_stream", "step_progress", "tool_output", "run_complete", "error"]
SSE_EVENT_TYPES = frozenset({"token_stream", "step_progress", "tool_output", "run_complete", "error"})
DEFAULT_HEARTBEAT_INTERVAL = 15.0


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """A version-neutral event envelope; event-specific fields live in data."""

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
            not self.event_id or any(char in self.event_id for char in "\r\n\0")
        ):
            raise ValueError("SSE event id must be non-empty and single-line")
        if self.retry_ms is not None and (
            isinstance(self.retry_ms, bool) or not isinstance(self.retry_ms, int)
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
    return ("\n".join(lines) + "\n\n").encode("utf-8")


HEARTBEAT_FRAME = b": heartbeat\n\n"


async def iter_sse(
    events: AsyncIterator[SSEEvent],
    *,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
) -> AsyncIterator[bytes]:
    """Yield encoded events and SSE comment heartbeats until the source completes."""
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


def sse_response(
    events: AsyncIterator[SSEEvent],
    *,
    heartbeat_interval: float = DEFAULT_HEARTBEAT_INTERVAL,
) -> StreamingResponse:
    """Build a no-buffering SSE response with the standard transport headers."""
    return StreamingResponse(
        iter_sse(events, heartbeat_interval=heartbeat_interval),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
