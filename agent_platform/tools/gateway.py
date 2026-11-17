"""Supervised tool gateway.

The tool registry and actor identity are trusted server-side inputs. Models
cannot choose a tool's risk level or provide proof of their own approval.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import monotonic
from typing import Any, Awaitable, Callable, Mapping, Protocol

from agent_platform.approval.policy import (
    ActionRequest,
    RiskLevel,
    SupervisedPolicy,
)
from agent_platform.observability.instrumentation import safe_metric, safe_observation, safe_update
from agent_platform.observability.trace_spans import trace_span
from agent_platform.tools.idempotency import ToolEffectStore


class ToolExecutionDenied(PermissionError):
    """Raised when a tool is unknown, unauthorized, or not approved."""


class ApprovalVerifier(Protocol):
    async def consume(
        self, *, tenant_id: str, approval_id: str, action_digest: str,
        policy_version: str = "1",
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
    correlation_id: str | None = None


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
    policy_version: str = "1"


class SupervisedToolGateway:
    def __init__(
        self,
        *,
        tools: tuple[ToolDefinition, ...],
        approval_verifier: ApprovalVerifier,
        audit_sink: AuditSink,
        idempotency_store: ToolEffectStore | None = None,
        observer: Any | None = None,
        metrics: Any | None = None,
    ) -> None:
        names = [tool.name for tool in tools]
        if len(names) != len(set(names)):
            raise ValueError("tool names must be unique")
        for tool in tools:
            if not isinstance(tool.policy_version, str) or not tool.policy_version.strip():
                raise ValueError(f"tool {tool.name!r} must declare a non-empty policy_version")
            if (
                not callable(tool.resource_resolver)
                or not callable(tool.resource_authorizer)
            ):
                raise ValueError(
                    f"tool {tool.name!r} must declare a trusted resource_resolver "
                    "and resource_authorizer; global tools must use an explicit "
                    "allow-all authorization policy"
                )
        self._tools = {tool.name: tool for tool in tools}
        self._policy = SupervisedPolicy({tool.name: tool.risk for tool in tools})
        self._approval_verifier = approval_verifier
        self._audit_sink = audit_sink
        self._idempotency_store = idempotency_store
        self._observer = observer
        self._metrics = metrics
        self._input_validators: dict[str, Any] = {}
        for tool in tools:
            if tool.input_schema is not None:
                try:
                    from jsonschema import Draft202012Validator
                except ImportError as exc:
                    raise RuntimeError("jsonschema is required for registered tool schemas") from exc
                Draft202012Validator.check_schema(dict(tool.input_schema))
                self._input_validators[tool.name] = Draft202012Validator(dict(tool.input_schema))

    @property
    def registered_tools(self) -> tuple[ToolDefinition, ...]:
        """Return immutable tool definitions for trusted composition code."""
        return tuple(self._tools.values())

    async def call_resolved(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        actor_roles: tuple[str, ...],
        tool_name: str,
        arguments: Mapping[str, Any],
        approval_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
        call_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Any:
        """Resolve the resource inside the trusted Gateway before authorization."""
        definition = self._tools.get(tool_name)
        if definition is None:
            raise ToolExecutionDenied("tool is not registered")
        resolver = definition.resource_resolver
        if resolver is None:
            raise ToolExecutionDenied("tool has no trusted resource ACL policy")
        try:
            resource = resolver(arguments)
        except Exception as exc:
            raise ToolExecutionDenied("tool resource could not be resolved") from exc
        if not isinstance(resource, str) or not resource.strip():
            raise ToolExecutionDenied("tool resource could not be resolved")
        return await self.call(
            tenant_id=tenant_id, actor_id=actor_id, actor_roles=actor_roles,
            tool_name=tool_name, resource=resource, arguments=arguments,
            approval_id=approval_id, run_id=run_id, agent_id=agent_id,
            call_id=call_id, correlation_id=correlation_id,
        )

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
        correlation_id: str | None = None,
    ) -> Any:
        definition = self._tools.get(tool_name)
        if definition is None:
            raise ToolExecutionDenied("tool is not registered")

        if definition.allowed_roles and not set(actor_roles).intersection(
            definition.allowed_roles
        ):
            raise ToolExecutionDenied("actor role is not allowed to call this tool")

        # Narrow the optional annotations as well as fail closed at runtime;
        # composition-time validation alone is not visible to static typing.
        resource_resolver = definition.resource_resolver
        resource_authorizer = definition.resource_authorizer
        if resource_resolver is None or resource_authorizer is None:
            raise ToolExecutionDenied("tool has no trusted resource ACL policy")

        # The resource identifier is derived from trusted server-side policy,
        # never accepted from an untrusted caller as the ACL authority.
        try:
            resolved_resource = resource_resolver(arguments)
        except Exception as exc:
            raise ToolExecutionDenied("tool resource could not be resolved") from exc
        if (
            not isinstance(resolved_resource, str)
            or not resolved_resource.strip()
            or resource != resolved_resource
        ):
            attempted_request = ActionRequest(
                action=tool_name,
                resource=str(resource)[:256],
                parameters=arguments,
                actor_id=actor_id,
                approval_id=approval_id,
            )
            await self._audit_sink.record(
                tenant_id=tenant_id, actor_id=actor_id, action=tool_name,
                resource=str(resource)[:256],
                action_digest=attempted_request.digest(),
                outcome="denied", run_id=run_id, agent_id=agent_id,
                detail="resource_resolution_mismatch",
            )
            raise ToolExecutionDenied("caller resource does not match trusted resource resolution")
        resource = resolved_resource
        request = ActionRequest(
            action=tool_name,
            resource=resource,
            parameters=arguments,
            actor_id=actor_id,
            approval_id=approval_id,
        )
        validator = self._input_validators.get(tool_name)
        if validator is not None:
            try:
                validator.validate(dict(arguments))
            except Exception as exc:
                await self._audit_sink.record(
                    tenant_id=tenant_id, actor_id=actor_id, action=tool_name,
                    resource=resource, action_digest=request.digest(), outcome="denied",
                    run_id=run_id, agent_id=agent_id, detail="input_schema_validation_failed",
                )
                raise ToolExecutionDenied("tool arguments do not match the registered schema") from exc

        execution_context = ToolExecutionContext(
            tenant_id=tenant_id,
            actor_id=actor_id,
            actor_roles=actor_roles,
            run_id=run_id,
            agent_id=agent_id,
            tool_name=tool_name,
            resource=resource,
            correlation_id=correlation_id,
        )
        try:
            resource_allowed = await resource_authorizer(execution_context, arguments)
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
        effects = self._idempotency_store if requires_idempotency else None
        run_key = run_id or ""
        call_key = call_id or ""
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
                if existing.state != "retryable":
                    raise ToolExecutionDenied(
                        "previous side effect is pending, failed or compensated and requires reconciliation"
                    )

        approved_hash = None
        if approval_id and definition.risk == RiskLevel.EXTERNAL_OR_IRREVERSIBLE:
            consumed = await self._approval_verifier.consume(
                tenant_id=tenant_id,
                approval_id=approval_id,
                action_digest=request.digest(),
                policy_version=definition.policy_version,
                run_id=run_id,
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
        if effects is not None:
            claimed = await effects.claim(
                tenant_id=tenant_id, run_id=run_key, call_id=call_key,
                action_digest=request.digest(), actor_id=actor_id,
            )
            if claimed.state == "succeeded":
                return claimed.result
            if claimed.state != "pending" or not claimed.created:
                raise ToolExecutionDenied("side effect is already claimed or requires reconciliation")
        started = monotonic()
        status = "failed"
        trace_metadata = {
            "tenant_id": tenant_id,
            "actor_id": actor_id,
            "run_id": run_id or "",
            "agent_id": agent_id or "",
            "tool_name": tool_name,
            "correlation_id": correlation_id or "",
        }
        try:
            try:
                with trace_span(
                    "tool.call", component="tool", tenant_id=tenant_id,
                    run_id=run_id, agent_id=agent_id, tool_name=tool_name,
                    correlation_id=correlation_id,
                    attributes={"call_id": call_id, "resource": resource},
                ), safe_observation(
                    self._observer,
                    name="tool.call",
                    input={"tool_name": tool_name, "argument_count": len(arguments)},
                    metadata=trace_metadata,
                ) as span:
                    try:
                        if definition.contextual_handler is not None:
                            result = await definition.contextual_handler(arguments, execution_context)
                        else:
                            result = await definition.handler(arguments)
                    except Exception as exc:
                        safe_update(span, output={
                            "status": "failed", "error_type": type(exc).__name__,
                        })
                        raise
                    status = "succeeded"
                    safe_update(span, output={
                        "status": status, "result_type": type(result).__name__,
                    })
            except Exception as exc:
                safe_metric(
                    self._metrics, "increment", "tool_failures_total",
                    labels={"component": "tool", "operation": "invoke", "status": "failed"},
                )
                if effects is not None:
                    await effects.fail(
                        tenant_id=tenant_id, run_id=run_key, call_id=call_key,
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
        finally:
            labels = {"component": "tool", "operation": tool_name}
            safe_metric(
                self._metrics, "increment", "tool_calls_total",
                labels={**labels, "status": status},
            )
            safe_metric(
                self._metrics, "observe", "tool_call_duration_seconds",
                monotonic() - started, labels=labels,
            )

        if effects is not None:
            await effects.complete(
                tenant_id=tenant_id, run_id=run_key, call_id=call_key, result=result,
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
