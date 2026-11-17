"""Per-call least-privilege authorization guard for runtime side effects.

Authorization is re-evaluated for every invocation. No positive decision is cached.
A denial or authorization backend failure interrupts the bound Run and prevents
the protected handler from being called.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol


class RuntimePermissionDenied(PermissionError):
    """Raised when a runtime invocation is not explicitly authorized."""


@dataclass(frozen=True)
class RuntimeCall:
    tenant_id: str
    run_id: str
    actor_id: str
    resource_type: str
    resource_id: str
    action: str

    def __post_init__(self) -> None:
        for name in ("tenant_id", "run_id", "actor_id", "resource_type", "resource_id", "action"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    policy_version: str
    reason: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool):
            raise ValueError("allowed must be bool")
        if not isinstance(self.policy_version, str) or not self.policy_version.strip():
            raise ValueError("policy_version must be non-empty")


class RuntimePermissionAuthorizer(Protocol):
    async def authorize(self, *, call: RuntimeCall) -> PermissionDecision:
        """Resolve current policy and return a decision for this exact call."""
        ...


class RuntimePermissionAudit(Protocol):
    async def record(
        self, *, tenant_id: str, run_id: str, actor_id: str, action: str,
        resource_type: str, resource_id: str, policy_version: str,
        outcome: str, reason: str = "",
    ) -> None:
        """Persist redacted authorization metadata; failures are fail-closed."""
        ...


InterruptRun = Callable[[str, str], Awaitable[None]]
RuntimeHandler = Callable[[Mapping[str, Any]], Awaitable[Any]]


class LeastPrivilegeRuntimeGuard:
    """Wrap a side-effect handler with a fresh ACL check on every invocation."""

    def __init__(
        self, *, authorizer: RuntimePermissionAuthorizer,
        audit: RuntimePermissionAudit, interrupt_run: InterruptRun,
    ) -> None:
        self._authorizer = authorizer
        self._audit = audit
        self._interrupt_run = interrupt_run

    async def invoke(
        self, *, call: RuntimeCall, handler: RuntimeHandler,
        arguments: Mapping[str, Any],
    ) -> Any:
        # Never accept a caller-provided allow flag or reuse a prior decision.
        try:
            decision = await self._authorizer.authorize(call=call)
            if not isinstance(decision, PermissionDecision):
                raise TypeError("authorizer returned an invalid decision")
        except Exception as exc:
            await self._deny_and_interrupt(
                call, policy_version="unknown",
                reason=f"authorization_unavailable:{type(exc).__name__}",
            )
            raise RuntimePermissionDenied("runtime authorization unavailable") from exc

        if not decision.allowed:
            await self._deny_and_interrupt(
                call, policy_version=decision.policy_version,
                reason=decision.reason or "permission_denied",
            )
            raise RuntimePermissionDenied("runtime permission denied")

        # Write-ahead authorization evidence must succeed before any side effect.
        try:
            await self._audit.record(
                tenant_id=call.tenant_id, run_id=call.run_id,
                actor_id=call.actor_id, action=call.action,
                resource_type=call.resource_type, resource_id=call.resource_id,
                policy_version=decision.policy_version, outcome="authorized",
            )
        except Exception as exc:
            await self._interrupt_safely(call, f"audit_unavailable:{type(exc).__name__}")
            raise RuntimePermissionDenied("authorization audit unavailable") from exc

        try:
            result = await handler(arguments)
        except Exception as exc:
            await self._record_result(call, decision.policy_version, "failed", type(exc).__name__)
            raise
        await self._record_result(call, decision.policy_version, "succeeded")
        return result

    async def _deny_and_interrupt(
        self, call: RuntimeCall, *, policy_version: str, reason: str,
    ) -> None:
        audit_error: Exception | None = None
        try:
            await self._audit.record(
                tenant_id=call.tenant_id, run_id=call.run_id,
                actor_id=call.actor_id, action=call.action,
                resource_type=call.resource_type, resource_id=call.resource_id,
                policy_version=policy_version, outcome="denied", reason=reason,
            )
        except Exception as exc:
            audit_error = exc
        await self._interrupt_safely(call, reason)
        if audit_error is not None:
            raise RuntimePermissionDenied("denial audit unavailable") from audit_error

    async def _interrupt_safely(self, call: RuntimeCall, reason: str) -> None:
        try:
            await self._interrupt_run(call.tenant_id, call.run_id)
        except Exception as exc:
            # The original authorization failure remains the public failure.
            raise RuntimePermissionDenied("Run interruption failed; execution remains denied") from exc

    async def _record_result(
        self, call: RuntimeCall, policy_version: str, outcome: str, reason: str = "",
    ) -> None:
        await self._audit.record(
            tenant_id=call.tenant_id, run_id=call.run_id,
            actor_id=call.actor_id, action=call.action,
            resource_type=call.resource_type, resource_id=call.resource_id,
            policy_version=policy_version, outcome=outcome, reason=reason,
        )
