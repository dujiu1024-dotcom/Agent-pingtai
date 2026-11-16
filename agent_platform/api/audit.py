"""Authorized, read-only audit query endpoints."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal


def create_audit_router(*, reader: Any, authorize_read: Callable[[Principal], Any]) -> APIRouter:
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
        return {"tenant_id": principal.tenant_id, "items": list(items)}

    return router
