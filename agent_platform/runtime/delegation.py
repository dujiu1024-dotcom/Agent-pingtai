"""Bounded, supervised child-Run delegation contract.

The platform delegates execution to a host-provided durable Run executor. This
module enforces tenant inheritance, allowlists, cycle/depth/fan-out limits and
stable idempotency keys; it never invokes an LLM or a tool directly.
"""
from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import NAMESPACE_URL, uuid5

from agent_platform.contracts import RunContext
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult


@dataclass(frozen=True)
class DelegationSpec:
    delegation_key: str
    agent_id: str
    task: str
    allowed_tools: tuple[str, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        for name, value in (
            ("delegation_key", self.delegation_key),
            ("agent_id", self.agent_id),
            ("task", self.task),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if len(self.task) > 50_000:
            raise ValueError("delegated task exceeds the input size limit")
        if not isinstance(self.allowed_tools, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.allowed_tools
        ):
            raise ValueError("allowed_tools must be a tuple of non-empty strings")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("delegated tool allowlist must not contain duplicates")
        if not isinstance(self.required_capabilities, tuple) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.required_capabilities
        ):
            raise ValueError("required_capabilities must be a tuple of non-empty strings")
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("required capabilities must not contain duplicates")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not math.isfinite(self.timeout_seconds)
            or self.timeout_seconds <= 0
        ):
            raise ValueError("timeout_seconds must be a finite positive number")


@dataclass(frozen=True)
class DelegationPolicy:
    allowed_agent_ids: frozenset[str]
    allowed_tool_ids: frozenset[str] = frozenset()
    max_depth: int = 3
    max_children: int = 5
    max_timeout_seconds: float = 180.0
    max_output_chars: int = 30_000

    def __post_init__(self) -> None:
        if not isinstance(self.allowed_agent_ids, frozenset) or not self.allowed_agent_ids or any(
            not isinstance(item, str) or not item.strip()
            for item in self.allowed_agent_ids
        ):
            raise ValueError("an explicit non-empty delegated Agent allowlist is required")
        if not isinstance(self.allowed_tool_ids, frozenset) or any(
            not isinstance(item, str) or not item.strip()
            for item in self.allowed_tool_ids
        ):
            raise ValueError("allowed_tool_ids must be a frozenset of non-empty strings")
        for name, value in (
            ("max_depth", self.max_depth),
            ("max_children", self.max_children),
            ("max_output_chars", self.max_output_chars),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(self.max_timeout_seconds, bool)
            or not isinstance(self.max_timeout_seconds, (int, float))
            or not math.isfinite(self.max_timeout_seconds)
            or not 0 < self.max_timeout_seconds <= 1800
        ):
            raise ValueError("max_timeout_seconds must be finite and in (0, 1800]")


@dataclass(frozen=True)
class DelegationResult:
    delegation_key: str
    child_run_id: str
    agent_id: str
    output_text: str
    usage: Mapping[str, int]
    trace_id: str | None


class ChildRunExecutor(Protocol):
    """Host adapter that persists child Runs and enforces Gateway-only tools."""

    async def execute_child(
        self, *, request: HarnessRequest, parent_context: RunContext,
        idempotency_key: str,
    ) -> HarnessResult:
        ...


class SupervisedDelegationService:
    def __init__(
        self, *, executor: ChildRunExecutor, policy: DelegationPolicy,
        expert_registry: Any,
    ) -> None:
        if expert_registry is None:
            raise ValueError("tenant-bound ExpertRegistry is required for delegation")
        self.executor = executor
        self.policy = policy
        self.expert_registry = expert_registry

    async def _child_request(
        self, parent: RunContext, spec: DelegationSpec
    ) -> tuple[HarnessRequest, str]:
        if spec.agent_id not in self.policy.allowed_agent_ids:
            raise PermissionError("delegated Agent is not in the supervised allowlist")
        if not set(spec.allowed_tools).issubset(self.policy.allowed_tool_ids):
            raise PermissionError("delegated tools exceed the supervised allowlist")
        registry_tenant = getattr(self.expert_registry, "tenant_id", None)
        if not isinstance(registry_tenant, str) or not registry_tenant.strip():
            raise PermissionError("delegated ExpertRegistry must be explicitly tenant-bound")
        if registry_tenant != parent.tenant_id:
            raise PermissionError("delegated ExpertRegistry tenant mismatch")
        profile = await self.expert_registry.get(spec.agent_id)
        if profile is None or not profile.enabled:
            raise PermissionError("delegated Agent is not active and published in this tenant")
        if not set(spec.required_capabilities).issubset(profile.capabilities):
            raise PermissionError("delegated Agent lacks required capabilities")
        if spec.timeout_seconds <= 0 or spec.timeout_seconds > self.policy.max_timeout_seconds:
            raise ValueError("delegation timeout exceeds the configured policy")

        metadata = dict(parent.metadata or {})
        depth = metadata.get("delegation_depth", 0)
        if isinstance(depth, bool) or not isinstance(depth, int) or depth < 0:
            raise ValueError("delegation_depth must be a non-negative integer")
        if depth >= self.policy.max_depth:
            raise PermissionError("maximum delegation depth exceeded")
        ancestors = metadata.get("ancestor_agent_ids", ())
        if not isinstance(ancestors, (list, tuple)):
            raise ValueError("ancestor_agent_ids must be a list or tuple")
        chain = tuple(str(item) for item in ancestors) or (parent.agent_id,)
        if parent.agent_id not in chain:
            chain = (*chain, parent.agent_id)
        if spec.agent_id in chain:
            raise PermissionError("delegation cycle detected")

        identity = f"{parent.tenant_id}:{parent.run_id}:{spec.delegation_key}"
        child_run_id = str(uuid5(NAMESPACE_URL, identity))
        child_metadata = {
            **metadata,
            "parent_run_id": parent.run_id,
            "delegation_key": spec.delegation_key,
            "delegation_depth": depth + 1,
            "ancestor_agent_ids": list(chain),
        }
        child_context = RunContext(
            tenant_id=parent.tenant_id,
            run_id=child_run_id,
            agent_id=spec.agent_id,
            model_template=parent.model_template,
            task_id=parent.task_id,
            project_id=parent.project_id,
            correlation_id=parent.correlation_id,
            metadata=child_metadata,
        )
        request = HarnessRequest(
            context=child_context,
            instructions="Execute the delegated task under the parent Run's supervised policy.",
            input_text=spec.task,
            allowed_tools=spec.allowed_tools,
            metadata={
                "parent_run_id": parent.run_id,
                "delegation_key": spec.delegation_key,
                "delegation_depth": depth + 1,
            },
        )
        return request, child_run_id

    async def delegate(
        self, *, parent: RunContext, requests: Sequence[DelegationSpec]
    ) -> tuple[DelegationResult, ...]:
        if not isinstance(requests, Sequence) or isinstance(requests, (str, bytes)):
            raise ValueError("delegation requests must be a sequence of DelegationSpec values")
        if not requests:
            return ()
        if any(not isinstance(item, DelegationSpec) for item in requests):
            raise ValueError("every delegation request must be a DelegationSpec")
        if len(requests) > self.policy.max_children:
            raise ValueError("delegation fan-out exceeds the configured limit")
        keys = [item.delegation_key for item in requests]
        if len(set(keys)) != len(keys):
            raise ValueError("delegation keys must be unique within a parent Run")

        prepared = await asyncio.gather(*(
            self._child_request(parent, item) for item in requests
        ))

        async def run_one(spec: DelegationSpec, request: HarnessRequest, child_run_id: str) -> DelegationResult:
            result = await asyncio.wait_for(
                self.executor.execute_child(
                    request=request,
                    parent_context=parent,
                    idempotency_key=f"{parent.tenant_id}:{parent.run_id}:{spec.delegation_key}",
                ),
                timeout=spec.timeout_seconds,
            )
            if len(result.output_text) > self.policy.max_output_chars:
                raise ValueError("delegated output exceeds the configured size limit")
            return DelegationResult(
                delegation_key=spec.delegation_key,
                child_run_id=child_run_id,
                agent_id=spec.agent_id,
                output_text=result.output_text,
                usage=dict(result.usage),
                trace_id=result.trace_id,
            )

        # A failed child invalidates the fan-out as a whole. Cancel and await
        # every sibling so no child Run continues detached from its parent.
        tasks = [
            asyncio.create_task(run_one(spec, request, child_run_id))
            for spec, (request, child_run_id) in zip(requests, prepared)
        ]
        try:
            return tuple(await asyncio.gather(*tasks))
        except BaseException:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
