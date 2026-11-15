"""Supervised tool gateway.

The tool registry and actor identity are trusted server-side inputs. Models
cannot choose a tool's risk level or provide proof of their own approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from agent_platform.tools.idempotency import EffectConflict, ToolEffectStore

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
        run_id: str | None = None,
        agent_id: str | None = None,
        detail: str = "",
    ) -> None:
        """Persist an audit record; failure must prevent execution."""
        ...


ToolHandler = Callable[[Mapping[str, Any]], Awaitable[Any]]
ResourceResolver = Callable[[Mapping[str, Any]], str]


@dataclass(frozen=True)
class ToolExecutionContext:
    tenant_id: str
    actor_id: str
    actor_roles: tuple[str, ...]
    run_id: str | None
    agent_id: str | None
    tool_name: str
    resource: str


ResourceAuthorizer = Callable[
    [ToolExecutionContext, Mapping[str, Any]], Awaitable[bool]
]
ContextualToolHandler = Callable[
    [Mapping[str, Any], ToolExecutionContext], Awaitable[Any]
]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    risk: RiskLevel
    handler: ToolHandler
    description: str = ""
    allowed_roles: tuple[str, ...] = ()
    input_schema: Mapping[str, Any] | None = None
    resource_resolver: ResourceResolver | None = None
    resource_authorizer: ResourceAuthorizer | None = None
    contextual_handler: ContextualToolHandler | None = None


class SupervisedToolGateway:
    def __init__(
        self,
        *,
        tools: tuple[ToolDefinition, ...],
        approval_verifier: ApprovalVerifier,
        audit_sink: AuditSink,
        idempotency_store: ToolEffectStore | None = None,
    ) -> None:
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        self._tools = {tool.name: tool for tool in tools}
        self._policy = SupervisedPolicy({tool.name: tool.risk for tool in tools})
        self._approval_verifier = approval_verifier
        self._audit_sink = audit_sink
        self._idempotency_store = idempotency_store

    @property
    def registered_tools(self) -> tuple[ToolDefinition, ...]:
        """Return immutable tool definitions for trusted composition code."""
        return tuple(self._tools.values())

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
        run_id: str | None = None,
        agent_id: str | None = None,
        call_id: str | None = None,
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
        execution_context = ToolExecutionContext(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_roles=actor_roles,
            run_id=run_id,
            agent_id=agent_id,
            tool_name=tool_name,
            resource=resource,
        )
        if definition.resource_authorizer is not None:
            try:
                resource_allowed = await definition.resource_authorizer(
                    execution_context, arguments
                )
            except Exception as exc:
                resource_allowed = False
                authorization_detail = type(exc).__name__
            else:
                authorization_detail = "resource access denied"
            if not resource_allowed:
                await self._audit_sink.record(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    action=tool_name,
                    resource=resource,
                    action_digest=request.digest(),
                    outcome="denied",
                    run_id=run_id,
                    agent_id=agent_id,
                    detail=authorization_detail,
                )
                raise ToolExecutionDenied("resource access is not authorized")

        requires_idempotency = definition.risk in {
            RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
            RiskLevel.FINANCIAL_OR_SECURITY,
        }
        if requires_idempotency:
            if self._idempotency_store is None or not run_id or not call_id:
                raise ToolExecutionDenied(
                    "durable idempotency requires an idempotency store, run_id and call_id"
                )
            existing = await self._idempotency_store.get(
                tenant_id=tenant_id, run_id=run_id, call_id=call_id,
            )
            if existing is not None:
                if existing.action_digest != request.digest() or existing.actor_id != actor_id:
                    raise ToolExecutionDenied("idempotency key conflicts with a different action")
                if existing.state == "succeeded":
                    await self._audit_sink.record(
                        tenant_id=tenant_id, actor_id=actor_id, action=tool_name,
                        resource=resource, action_digest=request.digest(),
                        outcome="replayed", run_id=run_id, agent_id=agent_id,
                    )
                    return existing.result
                raise ToolExecutionDenied(
                    "previous side effect is pending or failed and requires reconciliation"
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
                run_id=run_id,
                agent_id=agent_id,
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
            run_id=run_id,
            agent_id=agent_id,
        )
        if requires_idempotency:
            claimed = await self._idempotency_store.claim(
                tenant_id=tenant_id, run_id=run_id, call_id=call_id,
                action_digest=request.digest(), actor_id=actor_id,
            )
            if claimed.state == "succeeded":
                return claimed.result
            if claimed.state != "pending":
                raise ToolExecutionDenied("previous side effect requires reconciliation")
        try:
            if definition.contextual_handler is not None:
                result = await definition.contextual_handler(arguments, execution_context)
            else:
                result = await definition.handler(arguments)
        except Exception as exc:
            if requires_idempotency:
                await self._idempotency_store.fail(
                    tenant_id=tenant_id, run_id=run_id, call_id=call_id,
                    error=type(exc).__name__,
                )
            await self._audit_sink.record(
                tenant_id=tenant_id,
                actor_id=actor_id,
                action=tool_name,
                resource=resource,
                action_digest=request.digest(),
                outcome="failed",
                run_id=run_id,
                agent_id=agent_id,
                detail=type(exc).__name__,
            )
            raise

        if requires_idempotency:
            await self._idempotency_store.complete(
                tenant_id=tenant_id, run_id=run_id, call_id=call_id, result=result,
            )
        await self._audit_sink.record(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=tool_name,
            resource=resource,
            action_digest=request.digest(),
            outcome="succeeded",
            run_id=run_id,
            agent_id=agent_id,
        )
        return result
