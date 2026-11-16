"""ASGI middleware for request-scoped W3C trace context propagation."""
from __future__ import annotations

from typing import Any

from agent_platform.observability.trace_context import (
    TraceContextScope,
    extract_trace_context,
    new_trace_context,
)


class TraceContextASGIMiddleware:
    """Extract or create trace context and echo validated identifiers on responses.

    Incoming traceparent values are validated by the trace-context parser. A
    malformed value is never reflected; a fresh trace is created instead.
    ContextVar state is restored after each request, including exceptions.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        raw_headers = scope.get("headers", [])
        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in raw_headers
        }
        context = extract_trace_context(headers)
        if context is None:
            correlation_id = headers.get("x-correlation-id")
            try:
                context = new_trace_context(correlation_id=correlation_id)
            except ValueError:
                context = new_trace_context()

        request_scope = dict(scope)
        state = dict(request_scope.get("state") or {})
        state["trace_context"] = context
        state["correlation_id"] = context.correlation_id
        request_scope["state"] = state

        async def traced_send(message: dict[str, Any]) -> None:
            if message.get("type") == "http.response.start":
                outgoing = [
                    (key, value)
                    for key, value in message.get("headers", [])
                    if key.lower() not in (b"traceparent", b"x-correlation-id")
                ]
                outgoing.extend(
                    (key.encode("ascii"), value.encode("latin-1"))
                    for key, value in context.as_headers().items()
                )
                message = {**message, "headers": outgoing}
            await send(message)

        with TraceContextScope(context):
            await self.app(request_scope, receive, traced_send)
