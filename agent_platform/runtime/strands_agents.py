"""Experimental Strands Agents adapter for controlled, tool-free runtime comparison.

The platform retains tool authority in Tool Gateway. This adapter intentionally
constructs agents with no tools, no tool-directory loading, and no plugins.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from typing import Any, AsyncContextManager, Callable, Protocol

from agent_platform.contracts import RunContext
from agent_platform.models.templates import ModelTemplate, ModelTemplateRegistry
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult

ProviderConfigResolver = Callable[[ModelTemplate, RunContext], Mapping[str, Any]]
ModelFactory = Callable[..., Any]
AgentFactory = Callable[..., Any]


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
    ) -> None:
        self.templates = templates
        self.provider_config_resolver = provider_config_resolver
        self.agent_factory = agent_factory
        self.model_factory = model_factory
        self.gateway_tool_provider = gateway_tool_provider

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
            prompt = request.input_text
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
        )
