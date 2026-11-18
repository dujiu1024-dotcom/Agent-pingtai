"""Fail-closed audit boundary for authenticated and instance HTTP requests."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from agent_platform.observability.redaction import redact_payload
from agent_platform.security.audit_context import AuditActor, AuditSink


class AuditRequestASGIMiddleware:
    """Persist request intent and completion with an explicit nullable actor.

    Authenticated requests must resolve to a persisted actor. In no-auth mode,
    callers may opt in to instance auditing by supplying a resolver that returns
    an AuditActor with actor_id=None and an explicit instance tenant.
    """

    def __init__(
        self,
        app: Any,
        *,
        sink: AuditSink,
        resolve_actor: Callable[[Any], Any] | None,
        resolve_instance_actor: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        if not callable(getattr(sink, "record", None)):
            raise TypeError("audit sink must expose record()")
        if resolve_actor is not None and not callable(resolve_actor):
            raise TypeError("resolve_actor must be callable when supplied")
        if resolve_instance_actor is not None and not callable(resolve_instance_actor):
            raise TypeError("resolve_instance_actor must be callable when supplied")
        self.app = app
        self.sink = sink
        self.resolve_actor = resolve_actor
        self.resolve_instance_actor = resolve_instance_actor

    async def _resolve(self, resolver: Callable[..., Any], value: Any, *, instance: bool) -> AuditActor:
        result = resolver(value)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, AuditActor):
            kind = "instance actor" if instance else "authenticated actor"
            raise RuntimeError(f"{kind} could not be resolved to an AuditActor")
        if instance and result.actor_id is not None:
            raise RuntimeError("no-auth instance audit actor_id must be null")
        return result

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        state = scope.get("state") or {}
        principal = state.get("identity.principal")
        if principal is None:
            if self.resolve_instance_actor is None:
                await self.app(scope, receive, send)
                return
            actor = await self._resolve(self.resolve_instance_actor, scope, instance=True)
        else:
            if self.resolve_actor is None:
                raise RuntimeError("authenticated request auditing requires an actor resolver")
            actor = await self._resolve(self.resolve_actor, principal, instance=False)

        method = str(scope.get("method", "GET")).upper()
        action = "http.request"
        path = str(scope.get("path") or "/")[:256]
        trace = state.get("trace_context")
        correlation_id = getattr(trace, "correlation_id", None) or state.get("correlation_id")
        trace_id = getattr(trace, "trace_id", None)

        details = {
            "method": method,
            "path": path,
            "request_id": actor.request_id,
            "correlation_id": correlation_id,
            "trace_id": trace_id,
        }
        await self.sink.record(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            action=f"{action}.requested",
            subject_id="http-request",
            outcome="succeeded",
            detail=redact_payload(details),
        )

        status_code: int | None = None

        async def capture_send(message: dict[str, Any]) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = message.get("status")
            await send(message)

        failure: BaseException | None = None
        try:
            await self.app(scope, receive, capture_send)
        except BaseException as exc:
            failure = exc

        if failure is not None or isinstance(status_code, bool) or not isinstance(status_code, int):
            outcome = "failed"
        elif status_code >= 500:
            outcome = "failed"
        elif status_code >= 400:
            outcome = "denied"
        else:
            outcome = "succeeded"
        await self.sink.record(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            action=action,
            subject_id="http-request",
            outcome=outcome,
            detail=redact_payload({
                **details,
                "status_code": status_code,
            }),
        )
        if failure is not None:
            raise failure
