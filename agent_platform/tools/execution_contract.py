"""Authenticated, Run-bound execution contract for all platform tool dispatch.

Transport adapters authenticate credentials and resolve a trusted principal before
calling this contract. Authorization is checked against the live Run and its
tool allowlist; execution is delegated only to SupervisedToolGateway.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Mapping, Protocol

from agent_platform.tools.gateway import SupervisedToolGateway


class ToolCallDenied(PermissionError):
    """The credential, Run binding, or tool authorization is invalid."""


class ToolCallTimedOut(TimeoutError):
    """The Gateway call exceeded its configured deadline."""


class ToolCallCancelled(asyncio.CancelledError):
    """The Run or caller cancelled the Gateway call."""


@dataclass(frozen=True)
class GatewayPrincipal:
    tenant_id: str
    actor_id: str
    actor_roles: tuple[str, ...]
    run_id: str
    agent_id: str
    allowed_tool_names: tuple[str, ...]

    def __post_init__(self) -> None:
        for name in ("tenant_id", "actor_id", "run_id", "agent_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if len(self.allowed_tool_names) != len(set(self.allowed_tool_names)):
            raise ValueError("allowed tool names must be unique")
        if any(not isinstance(name, str) or not name.strip() for name in self.allowed_tool_names):
            raise ValueError("allowed tool names must be non-empty strings")
        object.__setattr__(self, "actor_roles", tuple(self.actor_roles))
        object.__setattr__(self, "allowed_tool_names", tuple(self.allowed_tool_names))


class GatewayContractAudit(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str, action: str,
        subject_id: str, outcome: str, detail: Mapping[str, Any] | None = None,
    ) -> None: ...


PrincipalResolver = Callable[[str], Awaitable[GatewayPrincipal | None]]
RunAuthorizer = Callable[[GatewayPrincipal], Awaitable[bool]]


class ToolGatewayContract:
    """Single dispatch boundary for authenticated, authorized, Run-scoped tools.

    The resolver must validate the bearer credential (including expiry/revocation)
    and return claims sourced from trusted server state, never model arguments.
    The Run authorizer must verify tenant, actor, agent and active Run ownership.
    """

    def __init__(
        self, *, gateway: SupervisedToolGateway,
        resolve_principal: PrincipalResolver,
        authorize_run: RunAuthorizer,
        audit: GatewayContractAudit,
    ) -> None:
        self._gateway = gateway
        self._resolve_principal = resolve_principal
        self._authorize_run = authorize_run
        self._audit = audit

    async def call(
        self, *, bearer_token: str, tool_name: str,
        arguments: Mapping[str, Any], call_id: str,
        timeout_seconds: float = 30.0,
        cancellation: asyncio.Event | None = None,
    ) -> Any:
        if not isinstance(bearer_token, str) or not bearer_token.strip():
            raise ToolCallDenied("a valid bearer credential is required")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ToolCallDenied("tool name is required")
        if not isinstance(call_id, str) or not call_id.strip():
            raise ToolCallDenied("call_id is required")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if cancellation is not None and cancellation.is_set():
            raise ToolCallCancelled("tool call was cancelled before dispatch")

        principal = await self._resolve_principal(bearer_token)
        if principal is None:
            raise ToolCallDenied("credential is invalid, expired, or revoked")
        if tool_name not in principal.allowed_tool_names:
            await self._audit.record(
                tenant_id=principal.tenant_id, actor_id=principal.actor_id,
                action="tool.call", subject_id=principal.run_id, outcome="denied",
                detail={"tool_name": tool_name, "reason": "run_tool_not_allowlisted"},
            )
            raise ToolCallDenied("tool is not allowlisted for this Run")
        try:
            authorized = await self._authorize_run(principal)
        except Exception as exc:
            authorized = False
            reason = type(exc).__name__
        else:
            reason = "run_binding_denied"
        if not authorized:
            await self._audit.record(
                tenant_id=principal.tenant_id, actor_id=principal.actor_id,
                action="tool.call", subject_id=principal.run_id, outcome="denied",
                detail={"tool_name": tool_name, "reason": reason},
            )
            raise ToolCallDenied("Run binding is not authorized")

        call = asyncio.create_task(self._gateway.call_resolved(
            tenant_id=principal.tenant_id, actor_id=principal.actor_id,
            actor_roles=principal.actor_roles, tool_name=tool_name,
            arguments=dict(arguments), run_id=principal.run_id,
            agent_id=principal.agent_id, call_id=call_id,
        ))
        cancel_waiter: asyncio.Task[bool] | None = None
        try:
            waiters: set[asyncio.Task[Any]] = {call}
            if cancellation is not None:
                cancel_waiter = asyncio.create_task(cancellation.wait())
                waiters.add(cancel_waiter)
            done, _ = await asyncio.wait(
                waiters, timeout=float(timeout_seconds),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if call in done:
                return await call
            call.cancel()
            try:
                await call
            except asyncio.CancelledError:
                pass
            if cancel_waiter is not None and cancel_waiter in done:
                await self._audit.record(
                    tenant_id=principal.tenant_id, actor_id=principal.actor_id,
                    action="tool.call", subject_id=principal.run_id, outcome="cancelled",
                    detail={"tool_name": tool_name, "call_id": call_id},
                )
                raise ToolCallCancelled("tool call was cancelled")
            await self._audit.record(
                tenant_id=principal.tenant_id, actor_id=principal.actor_id,
                action="tool.call", subject_id=principal.run_id, outcome="timed_out",
                detail={"tool_name": tool_name, "call_id": call_id,
                        "timeout_seconds": float(timeout_seconds)},
            )
            raise ToolCallTimedOut("tool call exceeded its deadline")
        except asyncio.CancelledError:
            if not call.done():
                call.cancel()
                try:
                    await call
                except asyncio.CancelledError:
                    pass
            await self._audit.record(
                tenant_id=principal.tenant_id, actor_id=principal.actor_id,
                action="tool.call", subject_id=principal.run_id, outcome="cancelled",
                detail={"tool_name": tool_name, "call_id": call_id,
                        "reason": "caller_cancelled"},
            )
            raise
        finally:
            if cancel_waiter is not None:
                cancel_waiter.cancel()
                try:
                    await cancel_waiter
                except asyncio.CancelledError:
                    pass
