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
from typing import Any, Mapping, Protocol, Sequence

from agent_platform.models.registry_resolver import (
    ModelRegistryError,
    RegistryModelResolver,
    ResolvedModelConfig,
)


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
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.resolver = resolver
        self.transport = transport
        self.usage_recorder = usage_recorder
        self.timeout_seconds = timeout_seconds

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
            try:
                raw = await self.transport.complete(
                    config=config, messages=messages,
                    request_parameters=parameters,
                    timeout_seconds=self.timeout_seconds,
                )
            except ModelTransportError as exc:
                last_error = exc
                continue
            choices = raw.get("choices")
            if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
                raise ModelRoutingError("model provider returned no completion choice")
            message = choices[0].get("message")
            content = message.get("content") if isinstance(message, Mapping) else None
            if not isinstance(content, str):
                raise ModelRoutingError("model provider returned non-text completion content")
            usage = raw.get("usage", {})
            if not isinstance(usage, Mapping):
                raise ModelRoutingError("model provider returned invalid usage metadata")
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
            return RoutedModelResponse(
                content=content, model_id=config.model_id,
                template_id=config.template_id, template_version=config.template_version,
                provider_id=config.provider_id, provider_version=config.provider_version,
                usage=dict(usage), fallback_used=index > 0,
            )
        raise ModelRoutingError("all pinned model routes failed") from last_error
