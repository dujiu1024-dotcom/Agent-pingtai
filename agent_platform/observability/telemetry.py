"""Bounded platform metrics and privacy-safe structured operational events."""
from __future__ import annotations

import json
import logging
import math
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from agent_platform.observability.redaction import redact_payload

_COMPONENTS = frozenset({"api", "run", "agent", "model", "prompt", "tool", "retrieval", "queue", "worker"})
_STATUSES = frozenset({"succeeded", "failed", "denied", "retrying", "cancelled", "timeout"})
_OPERATIONS = frozenset({"complete", "execute", "invoke", "dispatch", "retry", "fallback", "poll", "claim"})


def _number(value: int | float, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return float(value)


def bounded_labels(*, component: str, status: str | None = None, operation: str | None = None,
                   model_template: str | None = None, queue: str | None = None) -> dict[str, str]:
    if component not in _COMPONENTS:
        raise ValueError("unsupported metric component")
    labels = {"component": component}
    if status is not None:
        if status not in _STATUSES:
            raise ValueError("unsupported metric status")
        labels["status"] = status
    if operation is not None:
        if operation not in _OPERATIONS:
            raise ValueError("unsupported metric operation")
        labels["operation"] = operation
    if model_template is not None:
        if not isinstance(model_template, str) or not model_template or len(model_template) > 64:
            raise ValueError("model_template must be a bounded template key")
        labels["model_template"] = model_template
    if queue is not None:
        if not isinstance(queue, str) or not queue or len(queue) > 64:
            raise ValueError("queue must be a bounded configured name")
        labels["queue"] = queue
    return labels


class PlatformTelemetry:
    """Semantic metrics contract shared by API, model, tool and queue workers."""

    def __init__(self, metrics: Any) -> None:
        self.metrics = metrics

    def model_usage(self, *, input_tokens: int, output_tokens: int,
                    cost_usd: int | float | None = None, model_template: str = "unknown") -> None:
        labels = bounded_labels(component="model", status="succeeded", operation="complete",
                                model_template=model_template)
        self.metrics.increment("model_tokens_total",
                               _number(input_tokens, "input_tokens") + _number(output_tokens, "output_tokens"),
                               labels=labels)
        if cost_usd is not None:
            self.metrics.increment("model_cost_usd_total", _number(cost_usd, "cost_usd"), labels=labels)

    def operation(self, *, component: str, operation: str, status: str,
                  duration_seconds: int | float) -> None:
        labels = bounded_labels(component=component, operation=operation, status=status)
        self.metrics.increment("operations_total", labels=labels)
        self.metrics.observe("operation_duration_seconds", _number(duration_seconds, "duration_seconds"),
                             labels=labels)
        if status == "denied":
            self.metrics.increment("operation_rejections_total", labels=labels)

    def retry(self, *, component: str, operation: str) -> None:
        self.metrics.increment("operation_retries_total",
                               labels=bounded_labels(component=component, operation=operation, status="retrying"))

    def tool_failure(self, *, operation: str = "invoke") -> None:
        self.metrics.increment("tool_failures_total",
                               labels=bounded_labels(component="tool", operation=operation, status="failed"))

    def queue_depth(self, *, depth: int, queue: str = "default") -> None:
        self.metrics.set_gauge("queue_depth", _number(depth, "depth"),
                               labels=bounded_labels(component="queue", operation="poll", queue=queue))


class StructuredEventLogger:
    """Emit one-line JSON events; payload fields are allow-listed and redacted."""

    _ALLOWED_FIELDS = frozenset({
        "duration_ms", "attempt", "error_type", "model_template",
        "queue_depth", "trace_id", "span_id",
    })

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self.logger = logger or logging.getLogger("agent_platform.events")

    def emit(self, event: str, *, component: str, operation: str, status: str,
             fields: Mapping[str, Any] | None = None) -> None:
        if not isinstance(event, str) or not event or len(event) > 100:
            raise ValueError("event must be non-empty bounded text")
        labels = bounded_labels(component=component, operation=operation, status=status)
        safe_fields = dict(fields or {})
        if set(safe_fields) - self._ALLOWED_FIELDS:
            raise ValueError("unsupported structured log field")
        payload = redact_payload({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event": event, **labels, **safe_fields,
        }, max_text_chars=500)
        self.logger.info(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
