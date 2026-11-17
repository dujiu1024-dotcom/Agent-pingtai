"""Cross-component trace/span lifecycle with bounded, redacted dimensions.

The API is exporter-neutral and ContextVar-backed, so async child tasks inherit
the active span. W3C request context remains the source of the trace identity.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from datetime import datetime, timezone
from time import monotonic
from typing import Any, Callable, Mapping
from uuid import uuid4

from agent_platform.observability.redaction import redact_payload
from agent_platform.observability.trace_context import current_trace_context

_COMPONENTS = frozenset({
    "request", "run", "agent", "model", "prompt", "tool", "retrieval",
    "knowledge", "blackboard", "connector", "custom",
})
_current_span: ContextVar["TraceSpan | None"] = ContextVar(
    "platform_current_trace_span", default=None,
)


def _identifier(value: str | None, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"{name} must be a non-empty string of at most 256 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError(f"{name} contains control characters")
    return value


@dataclass
class TraceSpan:
    trace_id: str
    span_id: str
    parent_span_id: str | None
    name: str
    component: str
    tenant_id: str | None = None
    request_id: str | None = None
    run_id: str | None = None
    agent_id: str | None = None
    model_id: str | None = None
    prompt_id: str | None = None
    prompt_version: str | None = None
    tool_name: str | None = None
    knowledge_collection_id: str | None = None
    retrieval_id: str | None = None
    correlation_id: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    status: str = "unset"
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    ended_at: datetime | None = None
    duration_ms: float | None = None
    _started_clock: float = field(default_factory=monotonic, repr=False)

    def __post_init__(self) -> None:
        for field_name in (
            "tenant_id", "request_id", "run_id", "agent_id", "model_id",
            "prompt_id", "prompt_version", "tool_name",
            "knowledge_collection_id", "retrieval_id", "correlation_id",
        ):
            setattr(self, field_name, _identifier(getattr(self, field_name), field_name))
        self.attributes = redact_payload(dict(self.attributes), max_text_chars=1000)

    def finish(self, status: str = "ok", *, attributes: Mapping[str, Any] | None = None) -> None:
        if self.ended_at is not None:
            return
        if status not in {"ok", "error", "cancelled"}:
            raise ValueError("span status must be ok, error, or cancelled")
        if attributes:
            merged = {**dict(self.attributes), **dict(attributes)}
            self.attributes = redact_payload(merged, max_text_chars=1000)
        self.status = status
        self.ended_at = datetime.now(timezone.utc)
        self.duration_ms = max(0.0, (monotonic() - self._started_clock) * 1000)

    def as_dict(self) -> dict[str, Any]:
        return redact_payload({
            "trace_id": self.trace_id, "span_id": self.span_id,
            "parent_span_id": self.parent_span_id, "name": self.name,
            "component": self.component, "tenant_id": self.tenant_id,
            "request_id": self.request_id, "run_id": self.run_id,
            "agent_id": self.agent_id, "model_id": self.model_id,
            "prompt_id": self.prompt_id, "prompt_version": self.prompt_version,
            "tool_name": self.tool_name,
            "knowledge_collection_id": self.knowledge_collection_id,
            "retrieval_id": self.retrieval_id, "correlation_id": self.correlation_id,
            "attributes": dict(self.attributes), "status": self.status,
            "started_at": self.started_at.isoformat(),
            "ended_at": self.ended_at.isoformat() if self.ended_at else None,
            "duration_ms": self.duration_ms,
        })


def current_trace_span() -> TraceSpan | None:
    return _current_span.get()


class TraceSpanScope:
    """Context manager that closes and exports one span on every exit path."""

    def __init__(
        self, name: str, *, component: str, tenant_id: str | None = None,
        request_id: str | None = None, run_id: str | None = None,
        agent_id: str | None = None, model_id: str | None = None,
        prompt_id: str | None = None, prompt_version: str | None = None,
        tool_name: str | None = None, knowledge_collection_id: str | None = None,
        retrieval_id: str | None = None, correlation_id: str | None = None,
        attributes: Mapping[str, Any] | None = None,
        exporter: Callable[[Mapping[str, Any]], Any] | None = None,
    ) -> None:
        if not isinstance(name, str) or not name.strip() or len(name) > 256:
            raise ValueError("span name must be non-empty and at most 256 characters")
        if component not in _COMPONENTS:
            raise ValueError("unsupported span component")
        parent = current_trace_span()
        request = current_trace_context()
        self.span = TraceSpan(
            trace_id=(parent.trace_id if parent else
                      request.trace_id if request else uuid4().hex),
            span_id=uuid4().hex[:16],
            parent_span_id=(parent.span_id if parent else request.span_id if request else None),
            name=name, component=component, tenant_id=tenant_id,
            request_id=request_id, run_id=run_id, agent_id=agent_id,
            model_id=model_id, prompt_id=prompt_id, prompt_version=prompt_version,
            tool_name=tool_name, knowledge_collection_id=knowledge_collection_id,
            retrieval_id=retrieval_id,
            correlation_id=(correlation_id or (request.correlation_id if request else None)),
            attributes=attributes or {},
        )
        self._exporter = exporter
        self._token: Token[TraceSpan | None] | None = None

    def __enter__(self) -> TraceSpan:
        self._token = _current_span.set(self.span)
        return self.span

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        status = "cancelled" if exc_type is not None and exc_type.__name__ == "CancelledError" else (
            "error" if exc is not None else "ok"
        )
        self.span.finish(status, attributes={"error_type": exc_type.__name__} if exc_type else None)
        if self._token is not None:
            _current_span.reset(self._token)
            self._token = None
        if self._exporter is not None:
            try:
                self._exporter(self.span.as_dict())
            except Exception:
                # Telemetry is best effort and must not change application behavior.
                pass
        return False


def trace_span(
    name: str, *, component: str, tenant_id: str | None = None,
    request_id: str | None = None, run_id: str | None = None,
    agent_id: str | None = None, model_id: str | None = None,
    prompt_id: str | None = None, prompt_version: str | None = None,
    tool_name: str | None = None, knowledge_collection_id: str | None = None,
    retrieval_id: str | None = None, correlation_id: str | None = None,
    attributes: Mapping[str, Any] | None = None,
    exporter: Callable[[Mapping[str, Any]], Any] | None = None,
) -> TraceSpanScope:
    """Create a child span for any request/run/agent/model/prompt/tool/RAG boundary."""
    return TraceSpanScope(
        name, component=component, tenant_id=tenant_id, request_id=request_id,
        run_id=run_id, agent_id=agent_id, model_id=model_id, prompt_id=prompt_id,
        prompt_version=prompt_version, tool_name=tool_name,
        knowledge_collection_id=knowledge_collection_id, retrieval_id=retrieval_id,
        correlation_id=correlation_id, attributes=attributes, exporter=exporter,
    )
