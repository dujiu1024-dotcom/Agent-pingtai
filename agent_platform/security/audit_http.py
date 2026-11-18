"""Fail-closed audit boundary for authenticated HTTP API requests."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from agent_platform.observability.redaction import redact_payload
from agent_platform.security.audit_context import AuditActor, AuditSink


class AuditRequestASGIMiddleware:
    """Persist an authenticated request intent before entering application code.

    The completion record captures the final HTTP outcome. Unauthenticated
    requests are handled by the identity middleware and are not assigned a
    fabricated tenant or actor identity here.
    """

    def __init__(
        self,
        app: Any,
        *,
        sink: AuditSink,
        resolve_actor: Callable[[Any], Any],
    ) -> None:
        if not callable(getattr(sink, "record", None)):
            raise TypeError("audit sink must expose record()")
        if not callable(resolve_actor):
            raise TypeError("resolve_actor must be callable")
        self.app = app
        self.sink = sink
        self.resolve_actor = resolve_actor

    async def _actor(self, principal: Any) -> AuditActor | None:
        result = self.resolve_actor(principal)
        if inspect.isawaitable(result):
            result = await result
        if result is None:
            raise RuntimeError(
                "authenticated principal could not be resolved to an audit actor"
            )
        if not isinstance(result, AuditActor):
            raise TypeError("resolve_actor must return AuditActor or None")
        return result

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        state = scope.get("state") or {}
        principal = state.get("identity.principal")
        if principal is None:
            # Explicit actor=null policy: anonymous/instance requests are not
            # attributed to a user and are not written to the user-actor ledger.
            # Instance-level operational events must use a separate system audit
            # sink with a nullable actor column; never synthesize a user ID.
            await self.app(scope, receive, send)
            return
        actor = await self._actor(principal)
        if actor is None:
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method", "GET")).upper()
        action = "http.request"
        path = str(scope.get("path") or "/")[:256]
        trace = state.get("trace_context")
        correlation_id = getattr(trace, "correlation_id", None) or state.get("correlation_id")
        trace_id = getattr(trace, "trace_id", None)

        # This intent write is deliberately before downstream application code.
        await self.sink.record(
            tenant_id=actor.tenant_id,
            actor_id=actor.actor_id,
            action=f"{action}.requested",
            subject_id="http-request",
            outcome="succeeded",
            detail=redact_payload({
                "method": method,
                "path": path,
                "request_id": actor.request_id,
                "correlation_id": correlation_id,
                "trace_id": trace_id,
            }),
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
                "method": method,
                "path": path,
                "status_code": status_code,
                "request_id": actor.request_id,
                "correlation_id": correlation_id,
                "trace_id": trace_id,
            }),
        )
        if failure is not None:
            raise failure
