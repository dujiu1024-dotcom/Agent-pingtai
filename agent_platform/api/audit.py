"""Authorized, read-only audit query endpoints."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.observability.redaction import redact_payload


def create_audit_router(*, reader: Any, authorize_read: Callable[[Principal], Any], identity_reader: Any | None = None, run_budget_reader: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/audit", tags=["audit"])

    @router.get("/tool-events")
    async def list_tool_events(
        request: Request, limit: int = 100,
        actor_id: str | None = None, run_id: str | None = None,
    ):
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_read(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "audit read permission required")
        try:
            items = await reader.list_events(
                tenant_id=principal.tenant_id, limit=limit,
                actor_id=actor_id, run_id=run_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tenant_id": principal.tenant_id, "items": [redact_payload(dict(item)) for item in items]}


    @router.get("/run-budget-usage")
    async def list_run_budget_usage(request: Request, run_id: str):
        """Expose the Run budget ledger to authorized audit readers."""
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_read(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "audit read permission required")
        if run_budget_reader is None:
            raise HTTPException(503, "Run budget audit reader is not configured")
        try:
            report = await run_budget_reader.get_run_budget_report(principal.tenant_id, run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run budget record not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return report

    @router.get("/identity-events")
    async def list_identity_events(
        request: Request, limit: int = 100,
        actor_id: str | None = None, action: str | None = None,
    ):
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_read(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "audit read permission required")
        if identity_reader is None:
            raise HTTPException(503, "identity audit reader is not configured")
        try:
            items = await identity_reader.list_events(
                tenant_id=principal.tenant_id, limit=limit,
                actor_id=actor_id, action=action,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tenant_id": principal.tenant_id, "items": [redact_payload(dict(item)) for item in items]}


    @router.get("/identity-events")
    async def list_identity_events(
        request: Request, limit: int = 100,
        actor_id: str | None = None, action: str | None = None,
    ):
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_read(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "audit read permission required")
        if identity_reader is None:
            raise HTTPException(503, "identity audit reader is not configured")
        try:
            items = await identity_reader.list_events(
                tenant_id=principal.tenant_id, limit=limit,
                actor_id=actor_id, action=action,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tenant_id": principal.tenant_id, "items": list(items)}

    return router
