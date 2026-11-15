"""Supervised tool gateway.

The tool registry and actor identity are trusted server-side inputs. Models
cannot choose a tool's risk level or provide proof of their own approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from agent_platform.approval.policy import (
    ActionRequest,
    RiskLevel,
    SupervisedPolicy,
)


class ToolExecutionDenied(PermissionError):
    """Raised when a tool is unknown, unauthorized, or not approved."""


class ApprovalVerifier(Protocol):
    async def consume(
        self, *, tenant_id: str, approval_id: str, action_digest: str
    ) -> bool:
        """Atomically validate and consume a one-time approval."""
        ...


class AuditSink(Protocol):
    async def record(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource: str,
        action_digest: str,
        outcome: str,
        detail: str = "",
    ) -> None:
        """Persist an audit record; failure must prevent execution."""
        ...


ToolHandler = Callable[[Mapping[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    risk: RiskLevel
    handler: ToolHandler
    description: str = ""
    allowed_roles: tuple[str, ...] = ()


class SupervisedToolGateway:
    def __init__(
        self,
        *,
        tools: tuple[ToolDefinition, ...],
        approval_verifier: ApprovalVerifier,
        audit_sink: AuditSink,
    ) -> None:
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        self._tools = {tool.name: tool for tool in tools}
        self._policy = SupervisedPolicy({tool.name: tool.risk for tool in tools})
        self._approval_verifier = approval_verifier
        self._audit_sink = audit_sink

    async def call(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        actor_roles: tuple[str, ...],
        tool_name: str,
        resource: str,
        arguments: Mapping[str, Any],
        approval_id: str | None = None,
    ) -> Any:
        definition = self._tools.get(tool_name)
        if definition is None:
            raise ToolExecutionDenied("tool is not registered")

        if definition.allowed_roles and not set(actor_roles).intersection(
            definition.allowed_roles
        ):
            raise ToolExecutionDenied("actor role is not allowed to call this tool")

        request = ActionRequest(
            action=tool_name,
            resource=resource,
            parameters=arguments,
            actor_id=actor_id,
            approval_id=approval_id,
        )

        approved_hash = None
        if approval_id and definition.risk == RiskLevel.EXTERNAL_OR_IRREVERSIBLE:
            consumed = await self._approval_verifier.consume(
                tenant_id=tenant_id,
                approval_id=approval_id,
                action_digest=request.digest(),
            )
            if consumed:
                approved_hash = request.digest()

        decision = self._policy.evaluate(
            request, approved_action_hash=approved_hash
        )
        if not decision.allowed:
            await self._audit_sink.record(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=tool_name,
                resource=resource,
                action_digest=request.digest(),
                outcome="denied",
                detail=decision.reason,
            )
            raise ToolExecutionDenied(decision.reason)

        # Write-ahead audit: if this fails, the handler is never invoked.
        await self._audit_sink.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=tool_name,
            resource=resource,
            action_digest=request.digest(),
            outcome="started",
        )
        try:
            result = await definition.handler(arguments)
        except Exception as exc:
            await self._audit_sink.record(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=tool_name,
                resource=resource,
                action_digest=request.digest(),
                outcome="failed",
                detail=type(exc).__name__,
            )
            raise

        await self._audit_sink.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=tool_name,
            resource=resource,
            action_digest=request.digest(),
            outcome="succeeded",
        )
        return result
