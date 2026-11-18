"""Request-scoped audit context and explicit action audit boundary."""
from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class AuditActor:
    tenant_id: str
    actor_id: str | None
    request_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id is required")
        if self.actor_id is not None and (
            not isinstance(self.actor_id, str) or not self.actor_id.strip()
        ):
            raise ValueError("actor_id must be a non-empty string or None")
        if self.request_id is not None and (
            not isinstance(self.request_id, str) or not self.request_id.strip()
        ):
            raise ValueError("request_id must be a non-empty string when supplied")


_current_actor: ContextVar[AuditActor | None] = ContextVar("platform_audit_actor", default=None)


def current_audit_actor() -> AuditActor | None:
    """Return the actor bound to the current async execution context."""
    return _current_actor.get()


class AuditSink(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str | None, action: str, subject_id: str,
        outcome: str = "succeeded", detail: Mapping[str, Any] | None = None,
    ) -> None: ...


class AuditContext:
    """Bind an authenticated actor and provide a fail-closed action audit hook.

    Callers must use this boundary for privileged side effects. It deliberately
    does not infer actors from request payloads or expose mutable context state.
    """

    def __init__(self, actor: AuditActor) -> None:
        self.actor = actor
        self._token: Token[AuditActor | None] | None = None

    def __enter__(self) -> "AuditContext":
        self._token = _current_actor.set(self.actor)
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        if self._token is not None:
            _current_actor.reset(self._token)
            self._token = None

    async def record_action(
        self, sink: AuditSink, *, action: str, subject_id: str,
        outcome: str = "succeeded", detail: Mapping[str, Any] | None = None,
    ) -> None:
        if not isinstance(action, str) or not action.strip():
            raise ValueError("action is required")
        if not isinstance(subject_id, str) or not subject_id.strip():
            raise ValueError("subject_id is required")
        await sink.record(
            tenant_id=self.actor.tenant_id,
            actor_id=self.actor.actor_id,
            action=action,
            subject_id=subject_id,
            outcome=outcome,
            detail=detail,
        )
