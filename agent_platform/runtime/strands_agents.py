"""Experimental Strands Agents adapter with supervised Gateway-only tools.

The platform retains tool authority in Tool Gateway. Agents receive tools only
from a trusted, run-scoped Gateway MCP provider; tool-directory loading and
plugins remain disabled.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, Awaitable, Callable, Protocol

from agent_platform.contracts import RunContext
from agent_platform.models.templates import ModelTemplate, ModelTemplateRegistry
from agent_platform.runtime.protocols import (
    HarnessCapabilities,
    HarnessRequest,
    HarnessResult,
    MediaInput,
    MediaOutput,
)

ProviderConfigResolver = Callable[[ModelTemplate, RunContext], Mapping[str, Any]]
ModelFactory = Callable[..., Any]
AgentFactory = Callable[..., Any]
MediaResolver = Callable[[RunContext, MediaInput], Awaitable[bytes]]
MediaOutputWriter = Callable[[RunContext, str, str, bytes, str | None], Awaitable[MediaOutput]]


class GatewayToolProvider(Protocol):
    """Open a run-scoped MCP session whose tools all terminate at Tool Gateway."""

    def open_session(
        self, *, context: RunContext, allowed_tools: Sequence[str]
    ) -> AsyncContextManager[Sequence[Any]]:
        ...


@asynccontextmanager
async def _empty_async_tool_session():
    yield ()


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _usage(metrics: Any) -> dict[str, int]:
    raw = _value(metrics, "accumulated_usage", {}) or {}
    aliases = {
        "input_tokens": ("input_tokens", "inputTokens"),
        "output_tokens": ("output_tokens", "outputTokens"),
        "total_tokens": ("total_tokens", "totalTokens"),
    }
    result: dict[str, int] = {}
    for target, candidates in aliases.items():
        for candidate in candidates:
            value = _value(raw, candidate)
            if isinstance(value, (int, float)):
                result[target] = int(value)
                break
    return result


class StrandsAgentsAdapter:
    """Wrap Strands Agents SDK behind the platform HarnessAdapter contract."""

    def __init__(
        self,
        *,
        templates: ModelTemplateRegistry,
        provider_config_resolver: ProviderConfigResolver,
        agent_factory: AgentFactory | None = None,
        model_factory: ModelFactory | None = None,
        gateway_tool_provider: GatewayToolProvider | None = None,
        capabilities: HarnessCapabilities | None = None,
        media_resolver: MediaResolver | None = None,
        media_output_writer: MediaOutputWriter | None = None,
    ) -> None:
        self.templates = templates
        self.provider_config_resolver = provider_config_resolver
        self.agent_factory = agent_factory
        self.model_factory = model_factory
        self.gateway_tool_provider = gateway_tool_provider
        self.capabilities = capabilities or HarnessCapabilities()
        self.media_resolver = media_resolver
        self.media_output_writer = media_output_writer
        media_inputs = self.capabilities.input_modalities - {"text"}
        media_outputs = self.capabilities.output_modalities - {"text"}
        if media_inputs and media_resolver is None:
            raise ValueError("declared Strands media inputs require an authorized media_resolver")
        if media_outputs and media_output_writer is None:
            raise ValueError("declared Strands media outputs require a trusted artifact writer")

    @staticmethod
    def _media_format(media: MediaInput) -> str:
        subtype = media.mime_type.lower().split(";", 1)[0].split("/", 1)[1]
        aliases = {"x-wav": "wav", "mpeg": "mp3", "jpg": "jpeg"}
        return aliases.get(subtype, subtype)

    async def _build_prompt(self, request: HarnessRequest) -> str | list[dict[str, Any]]:
        if not request.media_inputs:
            return request.input_text
        requested = {item.modality for item in request.media_inputs}
        if request.input_text and "text" not in self.capabilities.input_modalities:
            raise RuntimeError("Strands Harness does not declare text input support")
        if not requested.issubset(self.capabilities.input_modalities):
            raise RuntimeError(
                "Strands Harness does not declare input modalities: "
                + ", ".join(sorted(requested - self.capabilities.input_modalities))
            )
        if self.media_resolver is None:
            raise RuntimeError("Strands media input requires an authorized media_resolver")
        blocks: list[dict[str, Any]] = []
        if request.input_text:
            blocks.append({"text": request.input_text})
        for media in request.media_inputs:
            data = await self.media_resolver(request.context, media)
            if not isinstance(data, bytes):
                raise TypeError("media_resolver must return bytes")
            if len(data) != media.size_bytes:
                raise ValueError("resolved media size does not match its authorized reference")
            digest = hashlib.sha256(data).hexdigest()
            if digest.lower() != media.sha256.lower():
                raise ValueError("resolved media checksum does not match its authorized reference")
            media_format = self._media_format(media)
            if media.modality == "pdf":
                block = {
                    "document": {
                        "format": "pdf",
                        "name": media.filename or "document.pdf",
                        "source": {"bytes": data},
                    }
                }
            else:
                block = {
                    media.modality: {
                        "format": media_format,
                        "source": {"bytes": data},
                    }
                }
            blocks.append(block)
        return blocks

    async def _persist_media_outputs(
        self, request: HarnessRequest, content: Sequence[Any]
    ) -> tuple[MediaOutput, ...]:
        outputs: list[MediaOutput] = []
        for block in content:
            if not isinstance(block, Mapping):
                continue
            for modality in ("image", "document", "audio", "video"):
                value = block.get(modality)
                if not isinstance(value, Mapping):
                    continue
                output_modality = "pdf" if modality == "document" else modality
                if output_modality not in self.capabilities.output_modalities:
                    raise RuntimeError(
                        f"Strands produced undeclared {output_modality} output"
                    )
                if self.media_output_writer is None:
                    raise RuntimeError("Strands media output requires a trusted artifact writer")
                source = value.get("source", {})
                raw = source.get("bytes") if isinstance(source, Mapping) else None
                if isinstance(raw, str):
                    try:
                        raw = base64.b64decode(raw, validate=True)
                    except ValueError as exc:
                        raise ValueError("Strands media output bytes are not valid base64") from exc
                if not isinstance(raw, bytes):
                    raise ValueError("Strands media output must contain raw bytes")
                media_format = str(value.get("format") or "")
                if modality == "document":
                    if media_format != "pdf":
                        raise ValueError("Strands document output currently supports PDF only")
                    mime_type = "application/pdf"
                else:
                    mime_type = f"{modality}/{media_format}"
                filename = value.get("name") if isinstance(value.get("name"), str) else None
                saved = await self.media_output_writer(
                    request.context, output_modality, mime_type, raw, filename
                )
                expected_digest = hashlib.sha256(raw).hexdigest()
                if (
                    not isinstance(saved, MediaOutput)
                    or saved.modality != output_modality
                    or saved.mime_type != mime_type
                    or saved.size_bytes != len(raw)
                    or saved.sha256.lower() != expected_digest
                ):
                    raise TypeError(
                        "media_output_writer must return a matching, verified MediaOutput"
                    )
                outputs.append(saved)
        return tuple(outputs)

    async def run(self, request: HarnessRequest) -> HarnessResult:
        if request.allowed_tools and self.gateway_tool_provider is None:
            raise RuntimeError(
                "Strands tools require a run-scoped MCP Tool Gateway provider"
            )
        template = self.templates.get(request.context.model_template)
        provider = dict(self.provider_config_resolver(template, request.context))
        unknown = set(provider) - {"api_key", "base_url", "timeout"}
        if unknown:
            raise ValueError(
                "Strands provider config contains unsupported keys: "
                + ", ".join(sorted(unknown))
            )
        if not provider.get("api_key") or not provider.get("base_url"):
            raise RuntimeError("Strands requires a scoped model proxy api_key and base_url")

        model_factory = self.model_factory
        if model_factory is None:
            try:
                from strands.models.openai import OpenAIModel
            except ImportError as exc:
                raise RuntimeError(
                    "Install strands-agents[openai] to use this adapter"
                ) from exc
            model_factory = OpenAIModel

        agent_factory = self.agent_factory
        if agent_factory is None:
            try:
                from strands import Agent
            except ImportError as exc:
                raise RuntimeError(
                    "Install strands-agents to use this adapter"
                ) from exc
            agent_factory = Agent

        parameters = template.parameters
        model_params = {
            key: parameters[key]
            for key in ("max_tokens", "temperature", "top_p")
            if key in parameters
        }
        client_args: dict[str, Any] = {
            "api_key": str(provider["api_key"]),
            "base_url": str(provider["base_url"]),
        }
        if provider.get("timeout") is not None:
            client_args["timeout"] = float(provider["timeout"])

        model = model_factory(
            model_id=template.model,
            client_args=client_args,
            params=model_params,
        )
        if request.allowed_tools:
            assert self.gateway_tool_provider is not None
            tool_session = self.gateway_tool_provider.open_session(
                context=request.context,
                allowed_tools=tuple(request.allowed_tools),
            )
        else:
            tool_session = _empty_async_tool_session()

        async with tool_session as gateway_tools:
            agent = agent_factory(
                model=model,
                system_prompt=request.instructions,
                tools=list(gateway_tools),
                load_tools_from_directory=False,
                plugins=[],
                callback_handler=None,
                context_manager=False,
                agent_id=f"{request.context.run_id}-{request.context.agent_id}",
            )
            prompt = await self._build_prompt(request)
            timeout = parameters.get("timeout_seconds")
            try:
                invoke = agent.invoke_async(prompt, idempotency_token=request.context.run_id)
                if timeout is None:
                    result = await invoke
                else:
                    result = await asyncio.wait_for(invoke, timeout=float(timeout))
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    "Strands agent invocation exceeded its template timeout"
                ) from exc
            finally:
                shutdown = getattr(agent, "shutdown_async", None)
                if callable(shutdown):
                    await shutdown()

        message = _value(result, "message", {}) or {}
        content = _value(message, "content", []) or []
        output = "\n".join(
            str(_value(block, "text", ""))
            for block in content
            if _value(block, "text")
        )
        media_outputs = await self._persist_media_outputs(request, content)
        if output and "text" not in self.capabilities.output_modalities:
            raise RuntimeError("Strands Harness does not declare text output support")
        metrics = _value(result, "metrics")
        accumulated = _value(metrics, "accumulated_metrics", {}) or {}
        latency = _value(accumulated, "latency_ms")
        metadata: dict[str, Any] = {
            "provider": template.provider,
            "model": template.model,
            "runtime": "strands-agents",
        }
        if isinstance(latency, (int, float)):
            metadata["latency_ms"] = int(latency)
        cycle_count = _value(metrics, "cycle_count")
        if isinstance(cycle_count, int):
            metadata["cycle_count"] = cycle_count

        return HarnessResult(
            output_text=output,
            usage=_usage(metrics),
            finish_reason=str(_value(result, "stop_reason", "unknown")),
            trace_id=request.context.correlation_id,
            metadata=metadata,
            media_outputs=media_outputs,
        )
