"""Authenticated tenant-scoped Run status and cancellation API."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import RunLifecycleManager

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
LifecycleResolver = Callable[[Principal], Awaitable[RunLifecycleManager]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]


def create_runs_router(
    *, resolve_principal: PrincipalResolver,
    resolve_lifecycle: LifecycleResolver,
    authorize: Authorizer,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/runs", tags=["runs"])

    async def context(request: Request, run_id: str, action: str):
        principal = await resolve_principal(request)
        if not await authorize(principal, "run", run_id, action):
            raise HTTPException(403, "Run access denied")
        lifecycle = await resolve_lifecycle(principal)
        return principal, lifecycle

    def serialize(run: Any) -> dict[str, Any]:
        return {
            "run_id": run.run_id, "status": run.status.value,
            "version": run.version, "checkpoint_ref": run.checkpoint_ref,
            "last_error": run.last_error,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        }

    @router.get("/{run_id}")
    async def get_run(run_id: str, request: Request):
        principal, lifecycle = await context(request, run_id, "read")
        run = await lifecycle.store.get(principal.tenant_id, run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        return serialize(run)

    @router.post("/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request):
        principal, lifecycle = await context(request, run_id, "cancel")
        try:
            run = await lifecycle.cancel(principal.tenant_id, run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return serialize(run)

    return router
