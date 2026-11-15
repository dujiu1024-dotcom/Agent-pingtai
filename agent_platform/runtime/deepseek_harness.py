"""Experimental adapter for the official DeepSeek Harness Python SDK.

This adapter is intentionally restricted to isolated, no-platform-tool PoCs.
The DSH runtime can execute code and commands; it is not the platform security
boundary. Production use requires a reviewed profile that routes all tools
through the platform gateway and a verified disposable execution sandbox.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Mapping

from agent_platform.contracts import RunContext
from agent_platform.models.templates import ModelTemplate, ModelTemplateRegistry
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult


WorkspaceResolver = Callable[[RunContext], str | Path]
HomeResolver = Callable[[RunContext], str | Path]
ProviderEnvironmentResolver = Callable[[ModelTemplate, RunContext], Mapping[str, str]]
HarnessFactory = Callable[..., Any]


class DeepSeekHarnessAdapter:
    def __init__(
        self,
        *,
        templates: ModelTemplateRegistry,
        workspace_resolver: WorkspaceResolver,
        home_resolver: HomeResolver,
        provider_environment_resolver: ProviderEnvironmentResolver,
        profile: str = "sdk-minimal",
        permission_mode: str = "read-only",
        patches: tuple[str, ...] = (),
        container_isolation_verified: bool = False,
        sdk_factory: HarnessFactory | None = None,
    ) -> None:
        self.templates = templates
        self.workspace_resolver = workspace_resolver
        self.home_resolver = home_resolver
        self.provider_environment_resolver = provider_environment_resolver
        self.profile = profile
        self.permission_mode = permission_mode
        self.patches = patches
        self.container_isolation_verified = container_isolation_verified
        self.sdk_factory = sdk_factory

    async def run(self, request: HarnessRequest) -> HarnessResult:
        if not self.container_isolation_verified:
            raise RuntimeError(
                "DeepSeek Harness requires verified container filesystem and network isolation"
            )
        if request.allowed_tools:
            raise RuntimeError(
                "DeepSeek Harness tool calls are disabled until a gateway-only profile is verified"
            )

        template = self.templates.get(request.context.model_template)
        if self.profile == "sdk-minimal" and template.provider != "deepseek-official":
            raise RuntimeError(
                "sdk-minimal only exposes the bundled DeepSeek provider; "
                "use a reviewed custom SDK profile for other providers"
            )
        if self.profile == "sdk" and template.provider != "deepseek-official" and not self.patches:
            raise RuntimeError(
                "a non-default provider requires an explicit reviewed SDK profile patch"
            )

        workspace = Path(self.workspace_resolver(request.context)).resolve()
        home = Path(self.home_resolver(request.context)).resolve()
        if not workspace.is_dir():
            raise ValueError(f"isolated workspace does not exist: {workspace}")
        home.mkdir(parents=True, exist_ok=True)

        provider_env = dict(
            self.provider_environment_resolver(template, request.context)
        )
        if not any(key.endswith("_BASE_URL") for key in provider_env):
            raise RuntimeError(
                "DeepSeek Harness must use a scoped model proxy; direct provider credentials are not allowed"
            )
        child_env = {
            **provider_env,
            "DSH_PERMISSION_MODE": self.permission_mode,
        }
        prompt = (
            f"Agent instructions:\n{request.instructions.strip()}\n\n"
            f"Task input:\n{request.input_text}"
        )
        return await asyncio.to_thread(
            self._run_sync,
            request,
            template,
            str(workspace),
            str(home),
            child_env,
            prompt,
        )

    def _run_sync(
        self,
        request: HarnessRequest,
        template: ModelTemplate,
        workspace: str,
        home: str,
        child_env: Mapping[str, str],
        prompt: str,
    ) -> HarnessResult:
        factory = self.sdk_factory
        if factory is None:
            try:
                from deepseek_harness import DeepSeekHarness
            except ImportError as exc:
                raise RuntimeError(
                    "Install deepseek-harness-sdk to use this Harness adapter"
                ) from exc
            factory = DeepSeekHarness

        reasoning = template.definition.get("reasoning", {})
        reasoning_effort = (
            reasoning.get("effort") if isinstance(reasoning, dict) else None
        )
        options: dict[str, Any] = {
            "dsh_home": home,
            "cwd": workspace,
            "runtime_cwd": workspace,
            "profile": self.profile,
            "model": template.model,
            "max_tokens": template.parameters.get("max_tokens"),
            "request_timeout_seconds": template.parameters.get("timeout_seconds"),
            "env": dict(child_env),
        }
        if template.provider and self.profile != "sdk-minimal":
            options["provider"] = template.provider
        if reasoning_effort:
            options["reasoning_effort"] = reasoning_effort
        if self.patches:
            options["patches"] = self.patches

        session_id = f"{request.context.run_id}-{request.context.agent_id}"
        with factory(**options) as harness:
            result = harness.run(prompt, session_id=session_id)

        events = tuple(getattr(result, "events", ()) or ())
        notifications = tuple(getattr(result, "notifications", ()) or ())
        return HarnessResult(
            output_text=str(getattr(result, "final_response", "") or ""),
            usage={},
            finish_reason=str(getattr(result, "finish_reason", None) or "unknown"),
            trace_id=request.context.correlation_id,
            metadata={
                "provider": template.provider,
                "model": template.model,
                "profile": self.profile,
                "session_id": session_id,
                "events": list(events),
                "notifications": list(notifications),
            },
        )
