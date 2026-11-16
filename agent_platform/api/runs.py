"""Authenticated tenant-scoped Run status and cancellation API."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import RunLifecycleManager

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
LifecycleResolver = Callable[[Principal], Awaitable[RunLifecycleManager]]
InterventionResolver = Callable[[Principal], Awaitable[Any]]
DetailResolver = Callable[[Principal], Awaitable[Any]]
ExecutorResolver = Callable[[Principal], Awaitable[Any]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]


def create_runs_router(
    *, resolve_principal: PrincipalResolver,
    resolve_lifecycle: LifecycleResolver,
    authorize: Authorizer,
    resolve_interventions: InterventionResolver | None = None,
    resolve_details: DetailResolver | None = None,
    resolve_executor: ExecutorResolver | None = None,
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
        if resolve_details is not None:
            details = await resolve_details(principal)
            detail = await details.get(tenant_id=principal.tenant_id, run_id=run_id)
            if detail is None:
                raise HTTPException(404, "Run not found")
            return dict(detail)
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



    if resolve_executor is not None:
        @router.post("/{run_id}/pause")
        async def pause_run(run_id: str, request: Request):
            principal, _ = await context(request, run_id, "pause")
            executor = await resolve_executor(principal)
            try:
                run = await executor.pause(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    reason="paused by authorized human operator",
                )
            except KeyError as exc:
                raise HTTPException(404, "Run not found") from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return serialize(run)

    @router.post("/{run_id}/resume")
    async def resume_run(run_id: str, request: Request):
        principal, lifecycle = await context(request, run_id, "resume")
        try:
            run = await lifecycle.request_resume(principal.tenant_id, run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return serialize(run)

    if resolve_interventions is not None:
        @router.post("/{run_id}/interventions", status_code=201)
        async def submit_intervention(run_id: str, request: Request):
            principal, _ = await context(request, run_id, "intervene")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "JSON object is required") from exc
            if not isinstance(body, dict) or not isinstance(body.get("payload"), dict):
                raise HTTPException(400, "kind and payload object are required")
            service = await resolve_interventions(principal)
            try:
                result = await service.submit(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    actor_id=principal.user_id, kind=str(body.get("kind") or ""),
                    payload=body["payload"],
                )
            except KeyError as exc:
                raise HTTPException(404, "Run not found") from exc
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return dict(result)

        @router.get("/{run_id}/interventions")
        async def list_interventions(run_id: str, request: Request):
            principal, _ = await context(request, run_id, "read")
            service = await resolve_interventions(principal)
            return {"items": await service.list_interventions(
                tenant_id=principal.tenant_id, run_id=run_id,
            )}

        @router.get("/{run_id}/events")
        async def list_run_events(run_id: str, request: Request, limit: int = 200):
            principal, _ = await context(request, run_id, "read")
            service = await resolve_interventions(principal)
            try:
                items = await service.list_events(
                    tenant_id=principal.tenant_id, run_id=run_id, limit=limit,
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {"items": items}

    return router
