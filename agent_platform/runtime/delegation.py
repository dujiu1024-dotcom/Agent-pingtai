"""Bounded, supervised child-Run delegation contract.

The platform delegates execution to a host-provided durable Run executor. This
module enforces tenant inheritance, allowlists, cycle/depth/fan-out limits and
stable idempotency keys; it never invokes an LLM or a tool directly.
"""
from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol
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
        if not self.delegation_key.strip() or not self.agent_id.strip() or not self.task.strip():
            raise ValueError("delegation_key, agent_id and task are required")
        if len(self.task) > 50_000:
            raise ValueError("delegated task exceeds the input size limit")
        if len(set(self.allowed_tools)) != len(self.allowed_tools):
            raise ValueError("delegated tool allowlist must not contain duplicates")
        if any(not item.strip() for item in self.required_capabilities):
            raise ValueError("required capabilities must be non-empty strings")
        if len(set(self.required_capabilities)) != len(self.required_capabilities):
            raise ValueError("required capabilities must not contain duplicates")


@dataclass(frozen=True)
class DelegationPolicy:
    allowed_agent_ids: frozenset[str]
    allowed_tool_ids: frozenset[str] = frozenset()
    max_depth: int = 3
    max_children: int = 5
    max_timeout_seconds: float = 180.0
    max_output_chars: int = 30_000

    def __post_init__(self) -> None:
        if not self.allowed_agent_ids or any(not item.strip() for item in self.allowed_agent_ids):
            raise ValueError("an explicit non-empty delegated Agent allowlist is required")
        if self.max_depth < 1 or self.max_children < 1 or self.max_output_chars < 1:
            raise ValueError("delegation limits must be positive")
        if not 0 < self.max_timeout_seconds <= 1800:
            raise ValueError("max_timeout_seconds must be in (0, 1800]")


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
        registry_tenant = getattr(self.expert_registry, "tenant_id", parent.tenant_id)
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
        if not requests:
            return ()
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

        return tuple(await asyncio.gather(*(
            run_one(spec, request, child_run_id)
            for spec, (request, child_run_id) in zip(requests, prepared)
        )))
