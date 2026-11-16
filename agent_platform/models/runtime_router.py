"""Production OpenAI-compatible model routing over immutable Run snapshots.

The router never selects a mutable "latest" registry version. Candidate template IDs
must all exist in the Run snapshot, and credentials are resolved only at invocation.
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
import urllib.error
import urllib.request
from dataclasses import dataclass
from time import monotonic
from typing import Any, Mapping, Protocol, Sequence

from agent_platform.models.registry_resolver import (
    ModelRegistryError,
    RegistryModelResolver,
    ResolvedModelConfig,
)
from agent_platform.observability.instrumentation import safe_metric, safe_observation, safe_update


class ModelRoutingError(RuntimeError):
    """No eligible pinned model route could complete the request."""


class ModelTransportError(RuntimeError):
    """A provider request failed without exposing response bodies or credentials."""


class ModelUsageRecordingError(RuntimeError):
    """The model ran but its usage could not be durably recorded."""


class OpenAICompatibleTransport(Protocol):
    async def complete(
        self, *, config: ResolvedModelConfig,
        messages: Sequence[Mapping[str, Any]],
        request_parameters: Mapping[str, Any],
        timeout_seconds: float,
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
    """Small stdlib HTTP transport; redirects are rejected and secrets never logged."""

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
    ) -> Mapping[str, Any]:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        reserved = {"model", "messages", "stream", "tools", "tool_choice", "api_key", "authorization", "base_url"}
        if reserved.intersection(request_parameters):
            raise ModelTransportError("request parameters cannot override model routing or credentials")
        return await asyncio.to_thread(
            self._complete_sync, config, messages, request_parameters, timeout_seconds,
        )

    def _complete_sync(
        self, config: ResolvedModelConfig,
        messages: Sequence[Mapping[str, Any]],
        request_parameters: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        reserved = {"model", "messages", "stream", "tools", "tool_choice", "api_key", "authorization", "base_url"}
        if reserved.intersection(config.parameters) or reserved.intersection(request_parameters):
            raise ModelTransportError("model parameters cannot override routing or tool controls")
        payload = {
            **dict(config.parameters),
            **dict(request_parameters),
            "model": config.model_id,
            "messages": [dict(message) for message in messages],
        }
        body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            config.base_url.rstrip("/") + "/chat/completions",
            data=body,
            headers={
                "Authorization": "Bearer " + config.api_key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            raise ModelTransportError(f"model provider returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ModelTransportError("model provider request failed") from None
        if len(raw) > self.max_response_bytes:
            raise ModelTransportError("model provider response exceeded size limit")
        try:
            result = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ModelTransportError("model provider returned invalid JSON") from None
        if not isinstance(result, dict):
            raise ModelTransportError("model provider returned an invalid response")
        return result


class OpenAICompatibleModelRouter:
    """Route a request through primary and explicitly pinned fallback templates."""

    def __init__(
        self, *, resolver: RegistryModelResolver,
        transport: OpenAICompatibleTransport,
        usage_recorder: UsageRecorder | None = None,
        timeout_seconds: float = 60,
        observer: Any | None = None,
        metrics: Any | None = None,
        quota_service: Any | None = None,
        token_estimator: Any | None = None,
        quota_cost_estimator: ModelCostEstimator | None = None,
        quota_scope_resolver: Any | None = None,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.resolver = resolver
        self.transport = transport
        self.usage_recorder = usage_recorder
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

    async def complete(
        self, *, snapshot: Any, tenant_id: str, run_id: str,
        template_id: str, messages: Sequence[Mapping[str, Any]],
        request_id: str, required_capabilities: Sequence[str] = (),
        data_level: str = "internal",
        fallback_template_ids: Sequence[str] = (),
        request_parameters: Mapping[str, Any] | None = None,
    ) -> RoutedModelResponse:
        if not tenant_id.strip() or not run_id.strip() or not request_id.strip():
            raise ValueError("tenant_id, run_id and request_id are required")
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
        for index, candidate in enumerate(candidates):
            try:
                config = await self.resolver.resolve(snapshot=snapshot, template_id=candidate)
            except ModelRegistryError as exc:
                # A malformed or absent pinned definition is configuration failure,
                # not a provider outage; never silently route around it.
                raise ModelRoutingError("pinned model configuration is invalid") from exc
            if not set(required_capabilities).issubset(config.capabilities):
                raise ModelRoutingError("pinned model lacks required capabilities")
            if data_level not in config.allowed_data_levels:
                raise ModelRoutingError("model is not approved for the requested data level")
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
            quota_reservation = None
            if self.quota_service is not None:
                estimate = self.token_estimator.estimate(
                    model_id=config.model_id,
                    messages=messages,
                    request_parameters=parameters,
                    context_window_tokens=config.context_window_tokens,
                )
                estimated_usage = {
                    "input_tokens": estimate.input_tokens,
                    "output_tokens": estimate.output_tokens,
                }
                estimated_micros = await self.quota_cost_estimator.estimate_micros(
                    model_id=config.model_id, usage=estimated_usage,
                )
                if (
                    not isinstance(estimated_micros, int)
                    or isinstance(estimated_micros, bool)
                    or estimated_micros < 0
                ):
                    raise ModelUsageRecordingError(
                        "model price card could not estimate a finite reservation cost"
                    )
                scope_type, scope_id = (
                    self.quota_scope_resolver(snapshot, config)
                    if self.quota_scope_resolver is not None
                    else ("tenant", tenant_id)
                )
                if (
                    not isinstance(scope_type, str) or not scope_type
                    or not isinstance(scope_id, str) or not scope_id
                ):
                    raise ModelRoutingError("model quota scope resolver returned an invalid scope")
                quota_reservation, _warnings = await self.quota_service.reserve_run(
                    tenant_id=tenant_id, run_id=run_id,
                    scope_type=scope_type, scope_id=scope_id,
                    estimated_tokens=estimate.total_tokens,
                    estimated_cost_usd=Decimal(estimated_micros) / Decimal(1_000_000),
                    estimated_input_tokens=estimate.input_tokens,
                    estimated_output_tokens=estimate.output_tokens,
                )
            try:
                with safe_observation(
                    self.observer, name="model.complete",
                    input=trace_input, metadata=metadata,
                ) as span:
                    try:
                        raw = await self.transport.complete(
                            config=config, messages=messages,
                            request_parameters=parameters,
                            timeout_seconds=self.timeout_seconds,
                        )
                    except ModelTransportError as exc:
                        safe_update(span, output={
                            "status": "failed", "error_type": type(exc).__name__,
                        })
                        raise
                    status = "response_received"
                    safe_update(span, output={"status": status})
            except ModelTransportError as exc:
                if quota_reservation is not None:
                    # The provider may have accepted the request before the transport failed.
                    # Keep the reservation and prohibit automatic fallback to avoid duplicate billing.
                    raise ModelUsageRecordingError(
                        "model transport outcome is ambiguous; quota reservation retained for reconciliation"
                    ) from exc
                last_error = exc
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
            if not isinstance(content, str):
                raise ModelRoutingError("model provider returned non-text completion content")
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
                    await self.quota_service.settle_run(
                        quota_reservation,
                        model_key=f"{config.template_id}@{config.template_version}",
                        input_tokens=input_tokens, output_tokens=output_tokens,
                        cost_usd=Decimal(actual_micros) / Decimal(1_000_000),
                        project_id=getattr(snapshot, "project_id", None),
                        agent_id=getattr(snapshot, "agent_id", None),
                        source="provider_reported",
                    )
                except Exception as exc:
                    # A successful provider response may be billable; retain reservation if settlement fails.
                    raise ModelUsageRecordingError(
                        "model response succeeded but quota settlement failed"
                    ) from exc
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
            if index > 0:
                safe_metric(
                    self.metrics, "increment", "model_fallbacks_total",
                    labels={**labels, "operation": "fallback"},
                )
            return RoutedModelResponse(
                content=content, model_id=config.model_id,
                template_id=config.template_id, template_version=config.template_version,
                provider_id=config.provider_id, provider_version=config.provider_version,
                usage=dict(usage), fallback_used=index > 0,
            )
        raise ModelRoutingError("all pinned model routes failed") from last_error
