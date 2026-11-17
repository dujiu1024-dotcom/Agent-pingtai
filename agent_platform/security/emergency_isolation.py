"""Emergency isolation controls and fail-closed runtime safety shell.

The shell is intended to wrap untrusted runtime handlers at the platform
boundary. Isolation state is supplied by a trusted control-plane repository;
it is never accepted from Agent arguments or environment variables.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping, Protocol

from agent_platform.tools.gateway import ToolExecutionContext


class IsolationScope(str, Enum):
    GLOBAL = "global"
    TENANT = "tenant"
    RUN = "run"


@dataclass(frozen=True)
class IsolationRecord:
    scope: IsolationScope
    target_id: str
    reason: str
    activated_at: datetime
    expires_at: datetime | None = None
    incident_id: str = ""

    def __post_init__(self) -> None:
        if not self.target_id or not self.reason.strip():
            raise ValueError("isolation target and reason are required")
        if self.activated_at.tzinfo is None:
            raise ValueError("activated_at must be timezone-aware")
        if self.expires_at is not None:
            if self.expires_at.tzinfo is None or self.expires_at <= self.activated_at:
                raise ValueError("expires_at must be timezone-aware and after activation")


@dataclass(frozen=True)
class IsolationSnapshot:
    records: tuple[IsolationRecord, ...]
    observed_at: datetime
    version: str

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None or not self.version:
            raise ValueError("snapshot timestamp and version are required")


class IsolationStateProvider(Protocol):
    async def load(self) -> IsolationSnapshot: ...


class IsolationAuditSink(Protocol):
    async def write(self, event: Mapping[str, Any]) -> None: ...


class EmergencyIsolationError(PermissionError):
    """Raised when a Run is isolated or isolation state cannot be trusted."""


def _active(record: IsolationRecord, now: datetime) -> bool:
    return record.activated_at <= now and (
        record.expires_at is None or now < record.expires_at
    )


def matching_isolation(
    snapshot: IsolationSnapshot, context: ToolExecutionContext, now: datetime
) -> IsolationRecord | None:
    """Return the highest-scope active isolation matching this execution."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    matches = []
    for record in snapshot.records:
        if not _active(record, now):
            continue
        if record.scope is IsolationScope.GLOBAL:
            matches.append(record)
        elif record.scope is IsolationScope.TENANT and record.target_id == context.tenant_id:
            matches.append(record)
        elif record.scope is IsolationScope.RUN and record.target_id == context.run_id:
            matches.append(record)
    priority = {IsolationScope.GLOBAL: 0, IsolationScope.TENANT: 1, IsolationScope.RUN: 2}
    return max(matches, key=lambda item: priority[item.scope], default=None)


class RuntimeSafetyShell:
    """Wrap a Gateway handler with emergency isolation and mandatory audit.

    Control-plane read errors, stale snapshots, and audit failures all deny
    execution. The wrapped handler is never called after an isolation match.
    """

    def __init__(
        self,
        handler: Callable[[Mapping[str, Any], ToolExecutionContext], Awaitable[Any]],
        state: IsolationStateProvider,
        audit: IsolationAuditSink,
        *,
        max_snapshot_age_seconds: int = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if max_snapshot_age_seconds < 1:
            raise ValueError("max_snapshot_age_seconds must be positive")
        self._handler = handler
        self._state = state
        self._audit = audit
        self._max_age = max_snapshot_age_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    async def __call__(
        self, arguments: Mapping[str, Any], context: ToolExecutionContext
    ) -> Any:
        now = self._clock()
        if now.tzinfo is None:
            raise EmergencyIsolationError("runtime safety clock is invalid")
        identity = {
            "tenant_id": context.tenant_id,
            "run_id": context.run_id,
            "actor_id": context.actor_id,
            "tool_name": context.tool_name,
        }
        try:
            snapshot = await self._state.load()
            age = (now - snapshot.observed_at).total_seconds()
            if age < 0 or age > self._max_age:
                raise EmergencyIsolationError("emergency isolation state is stale")
            record = matching_isolation(snapshot, context, now)
            if record is not None:
                await self._audit.write({
                    "event": "runtime.execution_blocked",
                    **identity,
                    "scope": record.scope.value,
                    "target_id": record.target_id,
                    "reason": record.reason,
                    "incident_id": record.incident_id,
                    "snapshot_version": snapshot.version,
                    "occurred_at": now.isoformat(),
                })
                raise EmergencyIsolationError(
                    f"execution blocked by {record.scope.value} emergency isolation"
                )
            await self._audit.write({
                "event": "runtime.execution_authorized",
                **identity,
                "snapshot_version": snapshot.version,
                "occurred_at": now.isoformat(),
            })
        except EmergencyIsolationError:
            raise
        except Exception as exc:
            raise EmergencyIsolationError(
                "emergency isolation control plane or audit unavailable"
            ) from exc

        try:
            result = await self._handler(arguments, context)
        except Exception as exc:
            await self._audit.write({
                "event": "runtime.execution_failed",
                **identity,
                "error_type": type(exc).__name__,
                "snapshot_version": snapshot.version,
                "occurred_at": self._clock().isoformat(),
            })
            raise
        await self._audit.write({
            "event": "runtime.execution_completed",
            **identity,
            "snapshot_version": snapshot.version,
            "occurred_at": self._clock().isoformat(),
        })
        return result
