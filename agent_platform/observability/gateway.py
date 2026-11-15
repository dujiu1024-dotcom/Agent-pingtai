"""Metrics decorator for the fail-closed Tool Gateway audit sink."""

from __future__ import annotations

from typing import Any, Mapping, Protocol


class AuditSink(Protocol):
    async def record(self, **kwargs: Any) -> None: ...


class MetricsPort(Protocol):
    def increment(self, name: str, value: float = 1.0, *, labels: Mapping[str, str] | None = None) -> None: ...


class MetricsAuditSink:
    """Record metrics only after durable audit succeeds; metrics failures fail open."""

    def __init__(self, inner: AuditSink, metrics: MetricsPort) -> None:
        self.inner = inner
        self.metrics = metrics

    async def record(self, **kwargs: Any) -> None:
        await self.inner.record(**kwargs)
        outcome = str(kwargs.get("outcome", "unknown"))
        action = str(kwargs.get("action", "unknown"))
        try:
            self.metrics.increment(
                "tool_audit_events_total",
                labels={"component": "tool_gateway", "status": outcome, "operation": action},
            )
            if outcome == "denied":
                self.metrics.increment(
                    "tool_denials_total",
                    labels={"component": "tool_gateway", "operation": action},
                )
        except Exception:
            # Observability must not change the fail-closed audit/execution contract.
            pass
