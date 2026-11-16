"""Validated W3C trace context propagation for platform component boundaries."""
from __future__ import annotations

import re
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Mapping
from uuid import uuid4

_TRACE_ID = re.compile(r"[0-9a-f]{32}\\Z")
_SPAN_ID = re.compile(r"[0-9a-f]{16}\\Z")
_FLAGS = re.compile(r"[0-9a-f]{2}\\Z")


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    span_id: str
    trace_flags: str = "01"
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        if not _TRACE_ID.fullmatch(self.trace_id) or self.trace_id == "0" * 32:
            raise ValueError("invalid trace_id")
        if not _SPAN_ID.fullmatch(self.span_id) or self.span_id == "0" * 16:
            raise ValueError("invalid span_id")
        if not _FLAGS.fullmatch(self.trace_flags):
            raise ValueError("invalid trace_flags")
        if self.correlation_id is not None and (
            not isinstance(self.correlation_id, str) or not self.correlation_id.strip()
        ):
            raise ValueError("invalid correlation_id")

    @property
    def traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"

    def as_headers(self) -> dict[str, str]:
        headers = {"traceparent": self.traceparent}
        if self.correlation_id:
            headers["x-correlation-id"] = self.correlation_id
        return headers


_current: ContextVar[TraceContext | None] = ContextVar("platform_trace_context", default=None)


def current_trace_context() -> TraceContext | None:
    return _current.get()


def new_trace_context(*, correlation_id: str | None = None) -> TraceContext:
    return TraceContext(uuid4().hex, uuid4().hex[:16], "01", correlation_id)


def extract_trace_context(headers: Mapping[str, str]) -> TraceContext | None:
    """Parse only version 00 traceparent; malformed input is ignored, never trusted."""
    raw = next((v for k, v in headers.items() if k.lower() == "traceparent"), None)
    if not isinstance(raw, str):
        return None
    parts = raw.split("-")
    if len(parts) != 4 or parts[0] != "00":
        return None
    correlation = next(
        (v for k, v in headers.items() if k.lower() == "x-correlation-id"), None
    )
    try:
        return TraceContext(parts[1], parts[2], parts[3], correlation)
    except ValueError:
        return None


class TraceContextScope:
    def __init__(self, context: TraceContext) -> None:
        self.context = context
        self._token = None

    def __enter__(self) -> TraceContext:
        self._token = _current.set(self.context)
        return self.context

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._token is not None:
            _current.reset(self._token)
            self._token = None
