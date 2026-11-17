"""Authenticated tenant-scoped Run replay endpoints."""
from __future__ import annotations
from typing import Any, Awaitable, Callable
from fastapi import APIRouter, HTTPException, Request
from agent_platform.identity.models import Principal
from agent_platform.runtime.replay import ReplayContextIncomplete, ReplayNotFound, RunReplayService

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
ReplayResolver = Callable[[Principal], Awaitable[RunReplayService]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]

def create_run_replay_router(*, resolve_principal: PrincipalResolver,
    resolve_replay: ReplayResolver, authorize: Authorizer) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/runs/{run_id}/replay", tags=["run-replay"])
    async def service(request: Request, run_id: str, action: str):
        principal = await resolve_principal(request)
        if not await authorize(principal, "run", run_id, action):
            raise HTTPException(403, "Run replay access denied")
        return principal, await resolve_replay(principal)
    def translate(exc: Exception) -> HTTPException:
        if isinstance(exc, ReplayNotFound): return HTTPException(404, str(exc))
        if isinstance(exc, ReplayContextIncomplete): return HTTPException(409, str(exc))
        if isinstance(exc, (KeyError, LookupError)): return HTTPException(404, "Run or replay snapshot not found")
        if isinstance(exc, ValueError): return HTTPException(409, str(exc))
        return HTTPException(500, "Replay operation failed")
    @router.post("")
    async def open_replay(run_id: str, request: Request):
        principal, replay = await service(request, run_id, "read")
        try:
            session = await replay.open(tenant_id=principal.tenant_id, run_id=run_id)
            return replay._state(session)
        except Exception as exc: raise translate(exc) from exc
    async def action(request: Request, run_id: str, session_id: str, name: str, **kwargs: Any):
        principal, replay = await service(request, run_id, "rerun" if name == "rerun" else "read")
        try:
            method = getattr(replay, name)
            return await method(tenant_id=principal.tenant_id, run_id=run_id,
                session_id=session_id, **kwargs)
        except Exception as exc: raise translate(exc) from exc
    @router.post("/{session_id}/step")
    async def step(run_id: str, session_id: str, request: Request):
        return await action(request, run_id, session_id, "step")
    @router.post("/{session_id}/pause")
    async def pause(run_id: str, session_id: str, request: Request):
        return await action(request, run_id, session_id, "pause")
    @router.post("/{session_id}/resume")
    async def resume(run_id: str, session_id: str, request: Request):
        return await action(request, run_id, session_id, "resume")
    @router.post("/{session_id}/fast-forward")
    async def fast_forward(run_id: str, session_id: str, request: Request):
        return await action(request, run_id, session_id, "fast_forward")
    @router.post("/{session_id}/back")
    async def back(run_id: str, session_id: str, request: Request):
        return await action(request, run_id, session_id, "back")
    async def requested_sequence(request: Request) -> int:
        try:
            body = await request.json()
        except (ValueError, UnicodeDecodeError) as exc:
            raise HTTPException(400, "request body must be valid JSON") from exc
        # bool is an int subclass in Python; reject it rather than treating true as sequence 1.
        if not isinstance(body, dict) or type(body.get("sequence")) is not int:
            raise HTTPException(400, "sequence integer is required")
        return body["sequence"]

    @router.post("/{session_id}/seek")
    async def seek(run_id: str, session_id: str, request: Request):
        sequence = await requested_sequence(request)
        return await action(request, run_id, session_id, "seek", sequence=sequence)

    @router.post("/{session_id}/rerun")
    async def rerun(run_id: str, session_id: str, request: Request):
        sequence = await requested_sequence(request)
        return await action(request, run_id, session_id, "rerun", sequence=sequence)
    return router
