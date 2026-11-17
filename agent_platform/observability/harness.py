"""Harness decorator that emits redacted traces and low-cardinality metrics."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from time import monotonic
from typing import Any, Mapping, Protocol

from agent_platform.observability.cost import ModelCostEstimator
from agent_platform.observability.metrics import InMemoryMetricsRecorder
from agent_platform.observability.redaction import redact_payload
from agent_platform.observability.trace_context import current_trace_context
from agent_platform.observability.trace_spans import current_trace_span, trace_span
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
        cost_estimator: ModelCostEstimator | None = None,
    ) -> None:
        if max_capture_chars < 1:
            raise ValueError("max_capture_chars must be positive")
        self.inner = inner
        self.observer = observer or NoopObserver()
        self.metrics = metrics or InMemoryMetricsRecorder()
        self.capture_content = capture_content
        self.max_capture_chars = max_capture_chars
        self.cost_estimator = cost_estimator

    @contextmanager
    def _safe_observation(self, *, name: str, input: Mapping[str, Any], metadata: Mapping[str, Any]):
        try:
            manager = self.observer.observe(name=name, input=input, metadata=metadata)
            span = manager.__enter__()
        except Exception:
            yield _NoopObservation()
            return
        try:
            yield span
        except BaseException as exc:
            try:
                manager.__exit__(type(exc), exc, exc.__traceback__)
            except Exception:
                pass
            raise
        else:
            try:
                manager.__exit__(None, None, None)
            except Exception:
                pass

    @staticmethod
    def _safe_update(span: Any, **kwargs: Any) -> None:
        try:
            span.update(**kwargs)
        except Exception:
            pass

    def _safe_metric(self, method: str, *args: Any, **kwargs: Any) -> None:
        try:
            getattr(self.metrics, method)(*args, **kwargs)
        except Exception:
            pass

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
        trace_context = current_trace_context()
        active_span = current_trace_span()
        if active_span is not None:
            metadata.update({
                "trace_id": active_span.trace_id,
                "span_id": active_span.span_id,
                "parent_span_id": active_span.parent_span_id,
                "trace_component": active_span.component,
            })
        if trace_context is not None:
            metadata.update({
                "trace_id": trace_context.trace_id,
                "span_id": trace_context.span_id,
                "traceparent": trace_context.traceparent,
                "correlation_id": trace_context.correlation_id or context.correlation_id,
            })
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
            with self._safe_observation(name="agent.run", input=trace_input, metadata=metadata) as span:
                try:
                    result = await self.inner.run(request)
                except asyncio.CancelledError:
                    status = "cancelled"
                    self._safe_update(span, output={"status": status})
                    raise
                except Exception as exc:
                    status = "failed"
                    self._safe_update(span, output={"status": status, "error_type": type(exc).__name__})
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
                input_tokens = int(result.usage.get("input_tokens", 0))
                output_tokens = int(result.usage.get("output_tokens", 0))
                cost_estimate = (
                    self.cost_estimator.estimate(
                        template, input_tokens=input_tokens, output_tokens=output_tokens
                    )
                    if self.cost_estimator is not None else None
                )
                if cost_estimate is not None:
                    output["estimated_cost_usd"] = str(cost_estimate)
                self._safe_update(span, output=redact_payload(output, max_text_chars=self.max_capture_chars))
                if input_tokens > 0:
                    self._safe_metric("increment", "agent_input_tokens_total", input_tokens, labels=labels)
                if output_tokens > 0:
                    self._safe_metric("increment", "agent_output_tokens_total", output_tokens, labels=labels)
                if result.tool_calls:
                    self._safe_metric("increment", "agent_tool_calls_total", len(result.tool_calls), labels=labels)
                if cost_estimate is not None and cost_estimate > 0:
                    self._safe_metric("increment", "agent_estimated_cost_usd_total", float(cost_estimate), labels=labels)
                return result
        finally:
            elapsed = monotonic() - started
            self._safe_metric("increment", "agent_runs_total", labels={**labels, "status": status})
            self._safe_metric("observe", "agent_run_duration_seconds", elapsed, labels=labels)
