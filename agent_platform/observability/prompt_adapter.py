"""Harness adapter that resolves immutable tenant-scoped prompt versions per Run."""
from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import replace
from typing import Any

from agent_platform.observability.prompts import tenant_prompt_key
from agent_platform.observability.trace_spans import trace_span


class PromptTemplateHarnessAdapter:
    """Render a pinned/canary Prompt Registry version before delegating model execution."""

    def __init__(
        self, inner: Any, *, registry: Any, prompt_id: str,
        variables_resolver: Callable[[Any], Mapping[str, str]],
        rollout_key_resolver: Callable[[Any], str] | None = None,
    ) -> None:
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise ValueError("prompt_id is required")
        if not callable(variables_resolver):
            raise ValueError("variables_resolver is required")
        self.inner = inner
        self.registry = registry
        self.prompt_id = prompt_id
        self.variables_resolver = variables_resolver
        self.rollout_key_resolver = rollout_key_resolver or (
            lambda request: request.context.run_id
        )

    async def run(self, request: Any) -> Any:
        tenant_id = request.context.tenant_id
        storage_key = tenant_prompt_key(tenant_id, self.prompt_id)
        variables = self.variables_resolver(request)
        if inspect.isawaitable(variables):
            variables = await variables
        if not isinstance(variables, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in variables.items()
        ):
            raise ValueError("prompt variables resolver must return a string-to-string mapping")
        rollout_key = self.rollout_key_resolver(request)
        if inspect.isawaitable(rollout_key):
            rollout_key = await rollout_key
        if not isinstance(rollout_key, str):
            raise ValueError("prompt rollout key must be a string")
        rendered = self.registry.render(
            storage_key, dict(variables), rollout_key=rollout_key,
        )
        if inspect.isawaitable(rendered):
            rendered = await rendered
        if not isinstance(rendered, tuple) or len(rendered) != 2:
            raise TypeError("prompt registry render() must return (text, version)")
        prompt_text, version = rendered
        if not isinstance(prompt_text, str) or not prompt_text:
            raise ValueError("prompt registry returned an empty rendered prompt")
        metadata = dict(getattr(request, "metadata", {}) or {})
        metadata.update({
            "prompt_id": self.prompt_id,
            "prompt_version": int(version.version),
            "prompt_content_hash": str(version.content_hash),
        })
        prompted_request = replace(
            request, instructions=prompt_text, metadata=metadata,
        )
        with trace_span(
            "prompt.render", component="prompt", tenant_id=tenant_id,
            run_id=request.context.run_id, agent_id=request.context.agent_id,
            prompt_id=self.prompt_id, prompt_version=str(version.version),
            attributes={"prompt_content_hash": str(version.content_hash),
                        "variable_count": len(variables)},
        ):
            return await self.inner.run(prompted_request)
