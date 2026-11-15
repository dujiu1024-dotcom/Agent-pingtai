"""Harness decorator that emits redacted traces and low-cardinality metrics."""

from __future__ import annotations

import asyncio
from time import monotonic
from typing import Any, Mapping, Protocol

from agent_platform.observability.metrics import InMemoryMetricsRecorder
from agent_platform.observability.redaction import redact_payload
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult


class HarnessRunner(Protocol):
    async def run(self, request: HarnessRequest) -> HarnessResult: ...


class MetricsPort(Protocol):
    def increment(self, name: str, value: float = 1.0, *, labels: Mapping[str, str] | None = None) -> None: ...
    def observe(self, name: str, value: float, *, labels: Mapping[str, str] | None = None) -> None: ...


class ObserverPort(Protocol):
    def observe(self, *, name: str, input: Mapping[str, Any], metadata: Mapping[str, Any]) -> Any: ...


class NoopObserver:
    def observe(self, *, name: str, input: Mapping[str, Any], metadata: Mapping[str, Any]):
        from contextlib import nullcontext
        return nullcontext(_NoopObservation())


class _NoopObservation:
    def update(self, **kwargs: Any) -> None:
        return None


class ObservedHarnessAdapter:
    """Decorate any HarnessAdapter without replacing its execution engine."""

    def __init__(
        self,
        inner: HarnessRunner,
        *,
        observer: ObserverPort | None = None,
        metrics: MetricsPort | None = None,
        capture_content: bool = False,
        max_capture_chars: int = 2000,
    ) -> None:
        if max_capture_chars < 1:
            raise ValueError("max_capture_chars must be positive")
        self.inner = inner
        self.observer = observer or NoopObserver()
        self.metrics = metrics or InMemoryMetricsRecorder()
        self.capture_content = capture_content
        self.max_capture_chars = max_capture_chars

    async def run(self, request: HarnessRequest) -> HarnessResult:
        context = request.context
        template = f"{context.model_template.template_id}@{context.model_template.version}"
        labels = {"component": "harness", "model_template": template}
        metadata = {
            "tenant_id": context.tenant_id,
            "run_id": context.run_id,
            "agent_id": context.agent_id,
            "model_template": template,
            "correlation_id": context.correlation_id,
        }
        trace_input: dict[str, Any] = {
            "input_chars": len(request.input_text),
            "instruction_chars": len(request.instructions),
            "allowed_tool_count": len(request.allowed_tools),
        }
        if self.capture_content:
            trace_input["input_text"] = request.input_text[: self.max_capture_chars]
            trace_input["instructions"] = request.instructions[: self.max_capture_chars]
        trace_input = redact_payload(trace_input, max_text_chars=self.max_capture_chars)
        started = monotonic()
        status = "succeeded"
        try:
            with self.observer.observe(name="agent.run", input=trace_input, metadata=metadata) as span:
                try:
                    result = await self.inner.run(request)
                except asyncio.CancelledError:
                    status = "cancelled"
                    span.update(output={"status": status})
                    raise
                except Exception as exc:
                    status = "failed"
                    span.update(output={"status": status, "error_type": type(exc).__name__})
                    raise
                output: dict[str, Any] = {
                    "status": status,
                    "output_chars": len(result.output_text),
                    "tool_call_count": len(result.tool_calls),
                    "finish_reason": result.finish_reason,
                    "usage": redact_payload(result.usage),
                }
                if self.capture_content:
                    output["output_text"] = result.output_text[: self.max_capture_chars]
                span.update(output=redact_payload(output, max_text_chars=self.max_capture_chars))
                input_tokens = int(result.usage.get("input_tokens", 0))
                output_tokens = int(result.usage.get("output_tokens", 0))
                if input_tokens > 0:
                    self.metrics.increment("agent_input_tokens_total", input_tokens, labels=labels)
                if output_tokens > 0:
                    self.metrics.increment("agent_output_tokens_total", output_tokens, labels=labels)
                if result.tool_calls:
                    self.metrics.increment("agent_tool_calls_total", len(result.tool_calls), labels=labels)
                return result
        finally:
            elapsed = monotonic() - started
            self.metrics.increment("agent_runs_total", labels={**labels, "status": status})
            self.metrics.observe("agent_run_duration_seconds", elapsed, labels=labels)
