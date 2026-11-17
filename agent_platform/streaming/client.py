"""Dependency-free SSE client decoder and reconnect cursor support."""
from __future__ import annotations

import codecs
import json
from collections.abc import Mapping
from typing import Any

from agent_platform.streaming.sse import SSE_EVENT_TYPES, SSEEvent


class SSEClient:
    """Incremental UTF-8 SSE parser that retains reconnect cursor and retry hints.

    Feed arbitrary network chunks with feed(); use reconnect_headers() when opening
    the next HTTP request after a disconnect. The transport remains caller-owned.
    """

    def __init__(self, *, default_retry_ms: int = 3000) -> None:
        if isinstance(default_retry_ms, bool) or default_retry_ms < 0:
            raise ValueError("default_retry_ms must be a non-negative integer")
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._buffer = ""
        self._data: list[str] = []
        self._event = ""
        self._event_id = ""
        self._last_event_id: str | None = None
        self._retry_ms = default_retry_ms

    @property
    def last_event_id(self) -> str | None:
        return self._last_event_id

    @property
    def retry_ms(self) -> int:
        return self._retry_ms

    def reconnect_headers(self) -> dict[str, str]:
        """Return headers for a reconnect; omit the cursor before the first event."""
        if self._last_event_id is None:
            return {"Accept": "text/event-stream"}
        return {
            "Accept": "text/event-stream",
            "Last-Event-ID": self._last_event_id,
        }

    def feed(self, chunk: bytes, *, final: bool = False) -> list[SSEEvent]:
        """Consume an arbitrary byte chunk and return all complete application events."""
        self._buffer += self._decoder.decode(chunk, final=final)
        events: list[SSEEvent] = []
        while True:
            newline = self._buffer.find("\n")
            if newline < 0:
                if final and self._buffer:
                    line, self._buffer = self._buffer, ""
                    self._process_line(line.rstrip("\r"), events)
                    self._dispatch(events)
                break
            line, self._buffer = self._buffer[:newline], self._buffer[newline + 1 :]
            self._process_line(line.rstrip("\r"), events)
        if final and not self._buffer:
            self._dispatch(events)
        return events

    def _process_line(self, line: str, events: list[SSEEvent]) -> None:
        if not line:
            self._dispatch(events)
            return
        if line.startswith(":"):
            return
        field, separator, value = line.partition(":")
        if separator and value.startswith(" "):
            value = value[1:]
        if field == "data":
            self._data.append(value)
        elif field == "event":
            self._event = value
        elif field == "id" and "\0" not in value:
            self._event_id = value
            self._last_event_id = value
        elif field == "retry" and value.isascii() and value.isdigit():
            self._retry_ms = int(value)

    def _dispatch(self, events: list[SSEEvent]) -> None:
        if not self._data:
            self._event = ""
            return
        event_name = self._event or "message"
        if event_name not in SSE_EVENT_TYPES:
            raise ValueError(f"unsupported SSE event type: {event_name!r}")
        payload: Any = json.loads("\n".join(self._data))
        if not isinstance(payload, Mapping):
            raise ValueError("SSE JSON data must be an object")
        events.append(
            SSEEvent(
                event=event_name,  # type: ignore[arg-type]
                data=payload,
                event_id=self._event_id or self._last_event_id,
                retry_ms=self._retry_ms,
            )
        )
        self._data.clear()
        self._event = ""
