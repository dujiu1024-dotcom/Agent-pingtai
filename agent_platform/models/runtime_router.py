"""Production OpenAI-compatible model routing over immutable Run snapshots.

The router never selects a mutable "latest" registry version. Candidate template IDs
must all exist in the Run snapshot, and credentials are resolved only at invocation.
"""
from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from agent_platform.models.circuit_breaker import CircuitOpenError
from agent_platform.models.context_overflow import (
    ContextOverflowError,
    HumanInterventionRequired,
    prepare_context,
)
from agent_platform.models.registry_resolver import (
    ModelRegistryError,
    RegistryModelResolver,
    ResolvedModelConfig,
)
from agent_platform.observability.instrumentation import safe_metric, safe_observation, safe_update
from agent_platform.observability.trace_context import current_trace_context
from agent_platform.observability.trace_spans import current_trace_span, trace_span


class ModelRoutingError(RuntimeError):
    """No eligible pinned model route could complete the request."""


class ModelTransportError(RuntimeError):
    """A provider request failed without exposing response bodies or credentials."""


class ModelUsageRecordingError(RuntimeError):
    """The model ran but its usage could not be durably recorded."""


class ModelAuditSink(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str, action: str,
        subject_id: str, outcome: str = "succeeded",
        detail: Mapping[str, Any] | None = None,
    ) -> None: ...


class OpenAICompatibleTransport(Protocol):
    async def complete(
        self, *, config: ResolvedModelConfig,
        messages: Sequence[Mapping[str, Any]],
        request_parameters: Mapping[str, Any],
        timeout_seconds: float,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> Mapping[str, Any]: ...


class UsageRecorder(Protocol):
    async def record(
        self, *, tenant_id: str, run_id: str, request_id: str,
        template_id: str, template_version: int, provider_id: str,
        provider_version: int, model_id: str, usage: Mapping[str, Any],
    ) -> None: ...


class ModelCostEstimator(Protocol):
    async def estimate_micros(
        self, *, model_id: str, usage: Mapping[str, Any],
    ) -> int | None: ...


class PostgresModelUsageRecorder:
    """Idempotently persist provider usage and optional host-priced cost."""

    def __init__(
        self, connection_factory: Any, *,
        cost_estimator: ModelCostEstimator | None = None,
    ) -> None:
        self._connect = connection_factory
        self.cost_estimator = cost_estimator

    async def record(
        self, *, tenant_id: str, run_id: str, request_id: str,
        template_id: str, template_version: int, provider_id: str,
        provider_version: int, model_id: str, usage: Mapping[str, Any],
    ) -> None:
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
        estimated_cost_micros = None
        if self.cost_estimator is not None:
            estimated_cost_micros = await self.cost_estimator.estimate_micros(
                model_id=model_id, usage=usage,
            )
            if estimated_cost_micros is not None and (
                not isinstance(estimated_cost_micros, int) or estimated_cost_micros < 0
            ):
                raise ValueError("estimated model cost must be a non-negative integer in micros")
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_model_usage_events
                   (tenant_id,run_id,request_id,template_id,template_version,
                    provider_id,provider_version,model_id,usage,estimated_cost_micros)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (tenant_id,run_id,request_id) DO NOTHING""",
                (tenant_id, run_id, request_id, template_id, template_version,
                 provider_id, provider_version, model_id, Jsonb(dict(usage)),
                 estimated_cost_micros),
            )


@dataclass(frozen=True)
class RoutedModelResponse:
    content: str
    tool_calls: Any
    model_id: str
    template_id: str
    template_version: int
    provider_id: str
    provider_version: int
    usage: Mapping[str, Any]
    fallback_used: bool


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Never forward Authorization to a provider-selected redirect target.
        return None


class OpenAICompatibleHTTPTransport:
    """OpenAI-compatible JSON and SSE transport with bounded streaming reads."""

    def __init__(self, *, max_response_bytes: int = 4 * 1024 * 1024) -> None:
        if max_response_bytes < 1024:
            raise ValueError("max_response_bytes must be at least 1024")
        self.max_response_bytes = max_response_bytes
        self._opener = urllib.request.build_opener(_NoRedirect())

    async def complete(
        self, *, config: ResolvedModelConfig,
        messages: Sequence[Mapping[str, Any]],
        request_parameters: Mapping[str, Any],
        timeout_seconds: float,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Any = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> Mapping[str, Any]:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        reserved = {"model", "messages", "stream", "stream_options", "tools", "tool_choice", "api_key", "authorization", "base_url"}
        if reserved.intersection(request_parameters):
            raise ModelTransportError("request parameters cannot override model routing or credentials")
        if on_token is None:
            return await asyncio.to_thread(
                self._complete_sync, config, messages, request_parameters, timeout_seconds,
                tools, tool_choice,
            )
        loop = asyncio.get_running_loop()

        def emit(token: str) -> None:
            future = asyncio.run_coroutine_threadsafe(on_token(token), loop)
            future.result(timeout=timeout_seconds)

        return await asyncio.to_thread(
            self._complete_sync, config, messages, request_parameters, timeout_seconds,
            tools, tool_choice, emit,
        )

    def _complete_sync(
        self, config: ResolvedModelConfig,
        messages: Sequence[Mapping[str, Any]],
        request_parameters: Mapping[str, Any],
        timeout_seconds: float,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Any = None,
        emit_token: Callable[[str], None] | None = None,
    ) -> Mapping[str, Any]:
        reserved = {"model", "messages", "stream", "stream_options", "api_key", "authorization", "base_url"}
        if reserved.intersection(config.parameters) or reserved.intersection(request_parameters):
            raise ModelTransportError("model parameters cannot override routing controls")
        streaming = emit_token is not None
        payload: dict[str, Any] = {
            **dict(config.parameters),
            **dict(request_parameters),
            "model": config.model_id,
            "messages": [dict(message) for message in messages],
        }
        if tools:
            payload["tools"] = [dict(t) for t in tools]
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        if streaming:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            config.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + config.api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if streaming else "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                if not streaming:
                    raw = response.read(self.max_response_bytes + 1)
                    if len(raw) > self.max_response_bytes:
                        raise ModelTransportError("model provider response exceeded size limit")
                    result = json.loads(raw)
                    if not isinstance(result, dict):
                        raise ModelTransportError("model provider returned an invalid response")
                    return result

                content: list[str] = []
                usage: Mapping[str, Any] | None = None
                consumed = 0
                while True:
                    remaining = self.max_response_bytes - consumed
                    raw_line = response.readline(remaining + 1)
                    if not raw_line:
                        break
                    if len(raw_line) > remaining:
                        raise ModelTransportError("model provider stream exceeded size limit")
                    consumed += len(raw_line)
                    line = raw_line.decode("utf-8").rstrip("\r\n")
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].lstrip(" ")
                    if data == "[DONE]":
                        break
                    try:
                        chunk = json.loads(data)
                    except (UnicodeDecodeError, json.JSONDecodeError):
                        raise ModelTransportError("model provider returned invalid stream JSON") from None
                    if not isinstance(chunk, Mapping):
                        raise ModelTransportError("model provider returned an invalid stream chunk")
                    chunk_usage = chunk.get("usage")
                    if isinstance(chunk_usage, Mapping):
                        usage = chunk_usage
                    choices = chunk.get("choices", [])
                    if not isinstance(choices, list):
                        raise ModelTransportError("model provider returned invalid stream choices")
                    for choice in choices:
                        if not isinstance(choice, Mapping):
                            continue
                        delta = choice.get("delta")
                        token = delta.get("content") if isinstance(delta, Mapping) else None
                        if isinstance(token, str) and token:
                            content.append(token)
                            if emit_token is not None:
                                emit_token(token)
                return {
                    "choices": [{"message": {"content": "".join(content)}}],
                    "usage": dict(usage or {}),
                }
        except urllib.error.HTTPError as exc:
            raise ModelTransportError(f"model provider returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ModelTransportError("model provider request failed") from None
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelTransportError("model provider returned invalid JSON") from None


class OpenAICompatibleModelRouter:
    """Route a request through primary and explicitly pinned fallback templates."""

    def __init__(
        self, *, resolver: RegistryModelResolver,
        transport: OpenAICompatibleTransport,
        usage_recorder: UsageRecorder | None = None,
        audit_sink: ModelAuditSink | None = None,
        timeout_seconds: float = 60,
        observer: Any | None = None,
        metrics: Any | None = None,
        quota_service: Any | None = None,
        token_estimator: Any | None = None,
        quota_cost_estimator: ModelCostEstimator | None = None,
        quota_scope_resolver: Any | None = None,
        circuit_breaker: Any | None = None,
        context_overflow_policy: str = "reject",
        context_summarizer: Any | None = None,
        context_intervention_handler: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.resolver = resolver
        self.transport = transport
        self.usage_recorder = usage_recorder
        self.audit_sink = audit_sink
        self.timeout_seconds = timeout_seconds
        self.observer = observer
        self.metrics = metrics
        if quota_service is not None and (
            token_estimator is None or quota_cost_estimator is None
        ):
            raise ValueError(
                "quota-enforced model routing requires a tokenizer and cost estimator"
            )
        self.quota_service = quota_service
        self.token_estimator = token_estimator
        self.quota_cost_estimator = quota_cost_estimator
        self.quota_scope_resolver = quota_scope_resolver
        self.circuit_breaker = circuit_breaker
        if context_overflow_policy not in {"reject", "truncate", "summarize", "human_intervention"}:
            raise ValueError("unsupported context overflow policy")
        self.context_overflow_policy = context_overflow_policy
        self.context_summarizer = context_summarizer
        self.context_intervention_handler = context_intervention_handler

    async def complete(
        self, *, snapshot: Any, tenant_id: str, run_id: str,
        template_id: str, messages: Sequence[Mapping[str, Any]],
        request_id: str, required_capabilities: Sequence[str] = (),
        data_level: str = "internal",
        fallback_template_ids: Sequence[str] = (),
        request_parameters: Mapping[str, Any] | None = None,
        protected_context: Mapping[str, Any] | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Any = None,
        on_token: Callable[[str], Awaitable[None]] | None = None,
    ) -> RoutedModelResponse:
        if not tenant_id.strip() or not run_id.strip() or not request_id.strip():
            raise ValueError("tenant_id, run_id and request_id are required")
        audit_sink = self.audit_sink
        if audit_sink is None or not callable(getattr(audit_sink, "record", None)):
            raise ModelUsageRecordingError("model routing requires a durable audit sink")

        async def record_preflight_failure(
            *, candidate_id: str, reason: str, outcome: str = "denied",
            error_type: str | None = None,
        ) -> None:
            detail: dict[str, Any] = {
                "phase": "preflight", "run_id": run_id,
                "template_id": candidate_id, "reason": reason,
            }
            if error_type is not None:
                detail["error_type"] = error_type
            try:
                await audit_sink.record(
                    tenant_id=tenant_id, actor_id="system:model-router",
                    action="model.response", subject_id=request_id,
                    outcome=outcome, detail=detail,
                )
            except Exception as exc:
                raise ModelUsageRecordingError(
                    "model preflight result could not be durably audited"
                ) from exc
        parameters = request_parameters or {}
        reserved = {
            "model", "messages", "stream", "tools", "tool_choice",
            "api_key", "authorization", "base_url",
        }
        if reserved.intersection(parameters):
            raise ModelRoutingError("request parameters cannot override model routing or credentials")
        candidates = (template_id, *tuple(fallback_template_ids))
        if len(candidates) != len(set(candidates)):
            raise ValueError("model route candidates must be unique")
        last_error: Exception | None = None
        stream_tokens_emitted = False
        for index, candidate in enumerate(candidates):
            # Write-ahead audit before registry resolution and quota checks so
            # rejected attempts are visible and no Provider can run unaudited.
            try:
                await audit_sink.record(
                    tenant_id=tenant_id, actor_id="system:model-router",
                    action="model.request", subject_id=request_id,
                    outcome="started",
                    detail={
                        "phase": "started", "run_id": run_id,
                        "template_id": candidate, "fallback_attempt": index > 0,
                    },
                )
            except Exception as exc:
                raise ModelUsageRecordingError(
                    "model request was not dispatched because write-ahead audit failed"
                ) from exc
            try:
                config = await self.resolver.resolve(snapshot=snapshot, template_id=candidate)
            except ModelRegistryError as exc:
                await record_preflight_failure(
                    candidate_id=candidate, reason="pinned_model_configuration_invalid",
                    outcome="failed", error_type=type(exc).__name__,
                )
                # A malformed or absent pinned definition is configuration failure,
                # not a provider outage; never silently route around it.
                raise ModelRoutingError("pinned model configuration is invalid") from exc
            if not set(required_capabilities).issubset(config.capabilities):
                await record_preflight_failure(
                    candidate_id=candidate, reason="required_capability_missing",
                )
                raise ModelRoutingError("pinned model lacks required capabilities")
            if data_level not in config.allowed_data_levels:
                await record_preflight_failure(
                    candidate_id=candidate, reason="data_level_not_allowed",
                )
                raise ModelRoutingError("model is not approved for the requested data level")
            breaker_key = f"{tenant_id}:{config.provider_id}:{config.model_id}"
            if self.circuit_breaker is not None:
                try:
                    allowed = await self.circuit_breaker.allow_request(breaker_key)
                except Exception as exc:
                    raise ModelRoutingError("model circuit breaker could not authorize dispatch") from exc
                if not allowed:
                    await record_preflight_failure(
                        candidate_id=candidate, reason="model_circuit_open",
                        outcome="denied", error_type="CircuitOpenError",
                    )
                    last_error = CircuitOpenError("model provider circuit is open")
                    continue
            started = monotonic()
            status = "failed"
            labels = {
                "component": "model",
                "model_template": f"{config.template_id}@{config.template_version}",
            }
            trace_input = {
                "message_count": len(messages),
                "request_parameter_count": len(parameters),
                "model_template": config.template_id,
                "model_template_version": config.template_version,
                "fallback_attempt": index > 0,
            }
            metadata = {
                "tenant_id": tenant_id,
                "run_id": run_id,
                "request_id": request_id,
                "provider_id": config.provider_id,
                "provider_version": config.provider_version,
                "model_id": config.model_id,
                "model_template": config.template_id,
                "model_template_version": config.template_version,
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
                    "correlation_id": trace_context.correlation_id,
                })
            quota_reservation = None
            # Context-window enforcement is independent of whether a tenant has
            # configured a monetary/token quota. When the host supplies an estimator,
            # every routed request must pass it before provider dispatch.
            estimate = None
            dispatch_messages = messages
            if self.token_estimator is not None:
                # Template defaults and per-call overrides are estimated together.
                effective_parameters = {**dict(config.parameters), **dict(parameters)}
                try:
                    prepared = await prepare_context(
                        estimator=self.token_estimator,
                        model_id=config.model_id,
                        messages=messages,
                        request_parameters=effective_parameters,
                        context_window_tokens=config.context_window_tokens,
                        policy=self.context_overflow_policy,
                        protected_context=protected_context,
                        summarizer=self.context_summarizer,
                        intervention_handler=self.context_intervention_handler,
                    )
                except HumanInterventionRequired as exc:
                    await record_preflight_failure(
                        candidate_id=candidate,
                        reason="human_intervention_required",
                        error_type=type(exc).__name__,
                    )
                    raise ModelRoutingError(str(exc)) from exc
                except ContextOverflowError as exc:
                    await record_preflight_failure(
                        candidate_id=candidate,
                        reason=(
                            "token_estimate_invalid_or_context_window_exceeded"
                            if self.context_overflow_policy == "reject"
                            else "context_overflow_policy_failed"
                        ),
                        error_type=type(exc).__name__,
                    )
                    raise ModelRoutingError(str(exc)) from exc
                except Exception as exc:
                    await record_preflight_failure(
                        candidate_id=candidate,
                        reason="token_estimation_failed",
                        error_type=type(exc).__name__,
                    )
                    # Unknown tokenizer/encoding failures remain fail-closed.
                    raise
                dispatch_messages = prepared.messages
                estimate = SimpleNamespace(
                    input_tokens=prepared.input_tokens,
                    output_tokens=prepared.output_tokens,
                    total_tokens=prepared.total_tokens,
                )
                if prepared.changed:
                    try:
                        await audit_sink.record(
                            tenant_id=tenant_id, actor_id="system:model-router",
                            action="model.context_overflow_resolved", subject_id=request_id,
                            outcome="succeeded",
                            detail={
                                "phase": "preflight", "run_id": run_id,
                                "template_id": candidate, "policy": prepared.policy,
                                "removed_messages": prepared.removed_messages,
                                "input_tokens": prepared.input_tokens,
                                "output_tokens": prepared.output_tokens,
                                "context_window_tokens": config.context_window_tokens,
                            },
                        )
                    except Exception as exc:
                        raise ModelUsageRecordingError(
                            "context overflow resolution could not be durably audited"
                        ) from exc
            if self.quota_service is not None:
                if estimate is None:
                    raise ModelRoutingError(
                        "quota-enforced model routing requires a token estimate"
                    )
                estimated_usage = {
                    "input_tokens": estimate.input_tokens,
                    "output_tokens": estimate.output_tokens,
                }
                try:
                    estimated_micros = await self.quota_cost_estimator.estimate_micros(
                        model_id=config.model_id, usage=estimated_usage,
                    )
                except Exception as exc:
                    await record_preflight_failure(
                        candidate_id=candidate, reason="quota_cost_estimation_failed",
                        outcome="failed", error_type=type(exc).__name__,
                    )
                    raise
                if (
                    not isinstance(estimated_micros, int)
                    or isinstance(estimated_micros, bool)
                    or estimated_micros < 0
                ):
                    await record_preflight_failure(
                        candidate_id=candidate, reason="quota_cost_estimate_invalid",
                        outcome="failed", error_type="ValueError",
                    )
                    raise ModelUsageRecordingError(
                        "model price card could not estimate a finite reservation cost"
                    )
                try:
                    scope_type, scope_id = (
                        self.quota_scope_resolver(snapshot, config)
                        if self.quota_scope_resolver is not None
                        else ("tenant", tenant_id)
                    )
                except Exception as exc:
                    await record_preflight_failure(
                        candidate_id=candidate, reason="quota_scope_resolution_failed",
                        outcome="failed", error_type=type(exc).__name__,
                    )
                    raise
                if (
                    not isinstance(scope_type, str) or not scope_type
                    or not isinstance(scope_id, str) or not scope_id
                ):
                    await record_preflight_failure(
                        candidate_id=candidate, reason="quota_scope_invalid",
                        outcome="failed", error_type="ValueError",
                    )
                    raise ModelRoutingError("model quota scope resolver returned an invalid scope")
                try:
                    quota_reservation, _warnings = await self.quota_service.reserve_run(
                        tenant_id=tenant_id, run_id=run_id,
                        scope_type=scope_type, scope_id=scope_id,
                        estimated_tokens=estimate.total_tokens,
                        estimated_cost_usd=Decimal(estimated_micros) / Decimal(1_000_000),
                        estimated_input_tokens=estimate.input_tokens,
                        estimated_output_tokens=estimate.output_tokens,
                    )
                except Exception as exc:
                    denied = type(exc).__name__ == "BudgetExceeded"
                    await record_preflight_failure(
                        candidate_id=candidate,
                        reason="quota_reservation_denied" if denied else "quota_reservation_failed",
                        outcome="denied" if denied else "failed",
                        error_type=type(exc).__name__,
                    )
                    raise
            try:
                with trace_span(
                    "model.complete", component="model", tenant_id=tenant_id,
                    request_id=request_id, run_id=run_id, model_id=config.model_id,
                    attributes={"provider_id": config.provider_id,
                                "model_template": config.template_id},
                ), safe_observation(
                    self.observer, name="model.complete",
                    input=trace_input, metadata=metadata,
                ) as span:
                    try:
                        if on_token is None:
                            raw = await self.transport.complete(
                                config=config, messages=dispatch_messages,
                                request_parameters=parameters,
                                timeout_seconds=self.timeout_seconds,
                                tools=tools, tool_choice=tool_choice,
                            )
                        else:
                            async def emit_stream_token(token: str) -> None:
                                nonlocal stream_tokens_emitted
                                stream_tokens_emitted = True
                                await on_token(token)
                            raw = await self.transport.complete(
                                config=config, messages=dispatch_messages,
                                request_parameters=parameters,
                                timeout_seconds=self.timeout_seconds,
                                tools=tools, tool_choice=tool_choice,
                                on_token=emit_stream_token,
                            )
                    except ModelTransportError as exc:
                        if self.circuit_breaker is not None:
                            await self.circuit_breaker.record_failure(breaker_key)
                        safe_update(span, output={
                            "status": "failed", "error_type": type(exc).__name__,
                        })
                        try:
                            await self.audit_sink.record(
                                tenant_id=tenant_id, actor_id="system:model-router",
                                action="model.response", subject_id=request_id,
                                outcome="failed",
                                detail={
                                    "run_id": run_id, "provider_id": config.provider_id,
                                    "model_id": config.model_id,
                                    "error_type": type(exc).__name__,
                                },
                            )
                        except Exception as audit_exc:
                            raise ModelUsageRecordingError(
                                "model transport failed and failure audit could not be persisted"
                            ) from audit_exc
                        raise
                    if self.circuit_breaker is not None:
                        await self.circuit_breaker.record_success(breaker_key)
                    status = "response_received"
                    safe_update(span, output={"status": status})
            except ModelTransportError as exc:
                if stream_tokens_emitted:
                    # Once tokens are visible to the user, fallback would duplicate
                    # partial output. Quota reservation alone doesn't block fallback
                    # — the reservation can be released when the candidate is abandoned.
                    raise ModelUsageRecordingError(
                        "streaming model outcome is ambiguous; fallback is prohibited"
                    ) from exc
                # Circuit breaker already recorded the failure. Move to next candidate
                # in the fallback list — but first cancel this candidate's quota
                # reservation if one was created; otherwise the next candidate's
                # reserve_run() hits the unique constraint on (run_id, scope).
                if quota_reservation is not None and hasattr(self.quota_service, "cancel_run"):
                    try:
                        await self.quota_service.cancel_run(reservation=quota_reservation)
                    except Exception:
                        pass  # Best-effort cleanup; don't mask the transport error
                quota_reservation = None
                last_error = exc
                safe_metric(
                    self.metrics, "increment", "model_retries_total",
                    labels={**labels, "operation": "retry"},
                )
                continue
            finally:
                safe_metric(
                    self.metrics, "increment", "model_requests_total",
                    labels={**labels, "status": status},
                )
                safe_metric(
                    self.metrics, "observe", "model_request_duration_seconds",
                    monotonic() - started, labels=labels,
                )
            choices = raw.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                raise ModelRoutingError("model provider returned no completion choice")
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, Mapping) else None
            tool_calls = message.get("tool_calls") if isinstance(message, Mapping) else None
            if not isinstance(content, str) and not tool_calls:
                # 附带可诊断的元信息，但不记录模型正文。
                finish_reason = choices[0].get("finish_reason")
                raise ModelRoutingError(
                    "model provider returned non-text completion content "
                    f"(finish_reason={finish_reason}, content_type={type(content).__name__})"
                )
            if not isinstance(content, str):
                content = ""
            # --- tool_calls 缺失 fallback ---
            # 调用者明确传了 tools 期望模型调工具，但模型返回纯文本回复
            # （finish_reason=stop 且 tool_calls 为空）。如果还有 fallback
            # candidate 可用，放弃这次（已 billable），换到下一模型重试。
            if (
                tools is not None
                and len(tools) > 0
                and not tool_calls
                and index + 1 < len(candidates)
                and not stream_tokens_emitted
            ):
                finish_reason = choices[0].get("finish_reason") if choices else None
                try:
                    await audit_sink.record(
                        tenant_id=tenant_id, actor_id="system:model-router",
                        action="model.response", subject_id=request_id,
                        outcome="failed",
                        detail={
                            "run_id": run_id,
                            "template_id": config.template_id,
                            "template_version": config.template_version,
                            "provider_id": config.provider_id,
                            "provider_version": config.provider_version,
                            "model_id": config.model_id,
                            "reason": "tool_calls_missing",
                            "finish_reason": finish_reason,
                            "fallback_attempt": index + 1,
                        },
                    )
                except Exception:
                    pass
                # 释放当前 quota reservation，让下一 candidate 重新 reserve
                if quota_reservation is not None and hasattr(
                    self.quota_service, "cancel_run"
                ):
                    try:
                        await self.quota_service.cancel_run(
                            reservation=quota_reservation
                        )
                    except Exception:
                        pass
                quota_reservation = None
                last_error = ModelRoutingError(
                    f"model {config.model_id} returned no tool_calls "
                    f"though {len(tools)} tools were offered "
                    f"(finish_reason={finish_reason}); "
                    f"falling back to {candidates[index + 1]}"
                )
                safe_metric(
                    self.metrics, "increment", "model_retries_total",
                    labels={**labels, "operation": "tool_calls_missing_retry"},
                )
                continue
            usage = raw.get("usage")
            if not isinstance(usage, Mapping):
                raise ModelUsageRecordingError(
                    "model response succeeded without provider usage metadata"
                )
            input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
            output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
            if (
                not isinstance(input_tokens, int) or isinstance(input_tokens, bool)
                or input_tokens < 0
                or not isinstance(output_tokens, int) or isinstance(output_tokens, bool)
                or output_tokens < 0
            ):
                # The provider has already returned a potentially billable result.
                # Do not fallback/retry or report zero usage: fail closed for
                # accounting and let the caller's reservation reconciliation run.
                raise ModelUsageRecordingError(
                    "model response contains missing or invalid token usage counters"
                )
            if quota_reservation is not None:
                normalized_usage = dict(usage)
                normalized_usage["input_tokens"] = input_tokens
                normalized_usage["output_tokens"] = output_tokens
                try:
                    actual_micros = await self.quota_cost_estimator.estimate_micros(
                        model_id=config.model_id, usage=normalized_usage,
                    )
                    if (
                        not isinstance(actual_micros, int)
                        or isinstance(actual_micros, bool)
                        or actual_micros < 0
                    ):
                        raise ValueError("actual model cost is unavailable")
                    price_card_version_resolver = getattr(
                        self.quota_cost_estimator, "price_card_version", None,
                    )
                    price_card_version = (
                        price_card_version_resolver(config.model_id)
                        if callable(price_card_version_resolver) else None
                    )
                    if price_card_version is not None and (
                        not isinstance(price_card_version, str) or not price_card_version.strip()
                    ):
                        raise ValueError("model price card version must be non-empty text")
                    await self.quota_service.settle_run(
                        quota_reservation,
                        model_key=config.model_id,
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cost_usd=Decimal(actual_micros) / Decimal(1_000_000),
                        project_id=getattr(snapshot, "project_id", None),
                        agent_id=getattr(snapshot, "agent_id", None),
                        price_card_version=price_card_version,
                        source="provider_reported",
                    )
                    safe_metric(
                        self.metrics, "increment", "model_cost_usd_total",
                        float(Decimal(actual_micros) / Decimal(1_000_000)),
                        labels=labels,
                    )
                except Exception as exc:
                    # A successful provider response may be billable; retain reservation if settlement fails.
                    raise ModelUsageRecordingError(
                        "model response succeeded but quota settlement failed"
                    ) from exc
            elif self.quota_cost_estimator is not None:
                # Cost telemetry is best-effort when quota enforcement is disabled.
                try:
                    estimated_micros = await self.quota_cost_estimator.estimate_micros(
                        model_id=config.model_id,
                        usage={**dict(usage), "input_tokens": input_tokens, "output_tokens": output_tokens},
                    )
                    if (
                        isinstance(estimated_micros, int)
                        and not isinstance(estimated_micros, bool)
                        and estimated_micros >= 0
                    ):
                        safe_metric(
                            self.metrics, "increment", "model_cost_usd_total",
                            float(Decimal(estimated_micros) / Decimal(1_000_000)),
                            labels=labels,
                        )
                except Exception:
                    pass
            safe_metric(
                self.metrics, "increment", "model_tokens_total",
                input_tokens + output_tokens, labels=labels,
            )
            if input_tokens > 0:
                safe_metric(
                    self.metrics, "increment", "model_input_tokens_total", input_tokens,
                    labels=labels,
                )
            if isinstance(output_tokens, int) and not isinstance(output_tokens, bool) and output_tokens > 0:
                safe_metric(
                    self.metrics, "increment", "model_output_tokens_total", output_tokens,
                    labels=labels,
                )
            if self.usage_recorder is not None:
                try:
                    await self.usage_recorder.record(
                        tenant_id=tenant_id, run_id=run_id, request_id=request_id,
                        template_id=config.template_id,
                        template_version=config.template_version,
                        provider_id=config.provider_id,
                        provider_version=config.provider_version,
                        model_id=config.model_id, usage=dict(usage),
                    )
                except Exception as exc:
                    # Do not retry another model after a successful billable response.
                    raise ModelUsageRecordingError(
                        "model response succeeded but usage recording failed"
                    ) from exc
            try:
                await self.audit_sink.record(
                    tenant_id=tenant_id, actor_id="system:model-router",
                    action="model.response", subject_id=request_id,
                    outcome="succeeded",
                    detail={
                        "run_id": run_id, "provider_id": config.provider_id,
                        "provider_version": config.provider_version,
                        "model_id": config.model_id,
                        "template_id": config.template_id,
                        "template_version": config.template_version,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                    },
                )
            except Exception as exc:
                # Provider work may already be billable. Do not fallback or retry.
                raise ModelUsageRecordingError(
                    "model response succeeded but result audit could not be persisted"
                ) from exc
            if index > 0:
                safe_metric(
                    self.metrics, "increment", "model_fallbacks_total",
                    labels={**labels, "operation": "fallback"},
                )
            return RoutedModelResponse(
                content=content, tool_calls=tool_calls,
                model_id=config.model_id,
                template_id=config.template_id, template_version=config.template_version,
                provider_id=config.provider_id, provider_version=config.provider_version,
                usage=dict(usage), fallback_used=index > 0,
            )
        raise ModelRoutingError("all pinned model routes failed") from last_error
