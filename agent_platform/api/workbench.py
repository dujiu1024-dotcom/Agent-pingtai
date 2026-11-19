"""Authenticated workbench capability and task-center API.

Task execution is delegated to the host's already-configured execution service;
this module does not create a second agent runtime or bypass Run/Gateway policy.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from agent_platform.identity.models import Principal


class TaskCenterService(Protocol):
    async def list_tasks(
        self, *, tenant_id: str, actor_id: str, limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def submit_task(
        self, *, tenant_id: str, actor_id: str, agent_id: str,
        project_id: str | None, session_id: str | None,
        input: Mapping[str, Any], idempotency_key: str | None = None,
        priority: int = 100,
    ) -> Mapping[str, Any]: ...

    async def get_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def list_task_runs(
        self, *, tenant_id: str, task_id: str,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def cancel_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def retry_task(
        self, *, tenant_id: str, task_id: str, actor_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any] | None: ...

    async def list_dead_letters(
        self, *, tenant_id: str, limit: int = 50, include_resolved: bool = False,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def get_dead_letter(
        self, *, tenant_id: str, run_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def retry_dead_letter(
        self, *, tenant_id: str, run_id: str, actor_id: str, idempotency_key: str,
    ) -> Mapping[str, Any] | None: ...

    async def resolve_dead_letter(
        self, *, tenant_id: str, run_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None: ...


PrincipalResolver = Callable[[Request], Awaitable[Principal]]
TaskServiceResolver = Callable[[Principal], Awaitable[TaskCenterService]]
TaskAuthorizer = Callable[[Principal, str, str], Awaitable[bool]]


def create_workbench_router(
    *,
    features: Mapping[str, bool],
    resolve_principal: PrincipalResolver | None = None,
    resolve_task_service: TaskServiceResolver | None = None,
    authorize_task: TaskAuthorizer | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/workbench", tags=["workbench"])
    snapshot = {str(key): bool(value) for key, value in features.items()}
    task_configured = all((
        resolve_principal is not None,
        resolve_task_service is not None,
        authorize_task is not None,
    ))
    if any(value is not None for value in (
        resolve_principal, resolve_task_service, authorize_task,
    )) and not task_configured:
        raise ValueError("task center principal, service resolver and authorizer must be configured together")
    if task_configured and not snapshot.get("task_execution", False):
        raise ValueError("task_execution capability must be enabled when task routes are mounted")

    @router.get("/capabilities")
    async def capabilities():
        return {"features": dict(snapshot)}

    if task_configured:
        async def task_context(request: Request, task_id: str, action: str):
            principal = await resolve_principal(request)
            if not await authorize_task(principal, task_id, action):
                raise HTTPException(403, "task access denied")
            service = await resolve_task_service(principal)
            bound_tenant = getattr(
                service, "tenant_id",
                getattr(getattr(service, "repository", None), "tenant_id", principal.tenant_id),
            )
            if bound_tenant != principal.tenant_id:
                raise HTTPException(403, "task service tenant mismatch")
            return principal, service

        @router.get("/tasks")
        async def list_tasks(request: Request, limit: int = 100):
            if not 1 <= limit <= 200:
                raise HTTPException(400, "limit must be between 1 and 200")
            principal, service = await task_context(request, "*", "read")
            try:
                items = await service.list_tasks(
                    tenant_id=principal.tenant_id, actor_id=principal.user_id,
                    limit=limit,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            return {"items": [dict(item) for item in items]}

        @router.post("/tasks", status_code=202)
        async def submit_task(request: Request):
            principal, service = await task_context(request, "*", "create")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "JSON object is required") from exc
            if not isinstance(body, dict):
                raise HTTPException(400, "JSON object is required")
            agent_id = str(body.get("agent_id") or "").strip()
            task_input = body.get("input")
            if not agent_id or not isinstance(task_input, dict) or not task_input:
                raise HTTPException(400, "agent_id and non-empty input object are required")
            project_id = str(body["project_id"]) if body.get("project_id") else None
            session_id = str(body["session_id"]) if body.get("session_id") else None
            if not await authorize_task(principal, agent_id, "execute"):
                raise HTTPException(403, "Agent execution access denied")
            if project_id and not await authorize_task(principal, project_id, "use_project"):
                raise HTTPException(403, "project access denied")
            if session_id and not await authorize_task(principal, session_id, "use_session"):
                raise HTTPException(403, "session access denied")
            try:
                priority = body.get("priority", 100)
                if type(priority) is not int or not 0 <= priority <= 1000:
                    raise HTTPException(422, "priority must be an integer between 0 and 1000")
                submit_kwargs: dict[str, Any] = {
                    "tenant_id": principal.tenant_id, "actor_id": principal.user_id,
                    "agent_id": agent_id, "project_id": project_id,
                    "session_id": session_id, "input": task_input,
                    "idempotency_key": (
                        str(body["idempotency_key"])
                        if body.get("idempotency_key") else None
                    ),
                    "priority": priority,
                }
                result = await service.submit_task(**submit_kwargs)
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            return dict(result)

        @router.get("/tasks/{task_id}")
        async def get_task(task_id: str, request: Request):
            principal, service = await task_context(request, task_id, "read")
            result = await service.get_task(
                tenant_id=principal.tenant_id, task_id=task_id,
                actor_id=principal.user_id,
            )
            if result is None:
                raise HTTPException(404, "task not found")
            return dict(result)

        @router.get("/tasks/{task_id}/runs")
        async def list_task_runs(task_id: str, request: Request):
            principal, service = await task_context(request, task_id, "read")
            # First confirm the task exists; returns 404 for missing tasks.
            task = await service.get_task(
                tenant_id=principal.tenant_id, task_id=task_id,
                actor_id=principal.user_id,
            )
            if task is None:
                raise HTTPException(404, "task not found")
            items = await service.list_task_runs(
                tenant_id=principal.tenant_id, task_id=task_id,
            )
            return {"items": [dict(item) for item in items]}

        @router.post("/tasks/{task_id}/retry")
        async def retry_task(task_id: str, request: Request):
            principal, service = await task_context(request, task_id, "retry")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "retry request body must be JSON") from exc
            idempotency_key = body.get("idempotency_key") if isinstance(body, Mapping) else None
            if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                raise HTTPException(422, "idempotency_key is required for task retry")
            try:
                result = await service.retry_task(
                    tenant_id=principal.tenant_id, task_id=task_id,
                    actor_id=principal.user_id, idempotency_key=idempotency_key,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            if result is None:
                raise HTTPException(404, "task not found")
            return JSONResponse(dict(result), status_code=202)

        @router.get("/dead-letters")
        async def list_dead_letters(
            request: Request, limit: int = 50, include_resolved: bool = False,
        ):
            if not 1 <= limit <= 200:
                raise HTTPException(400, "limit must be between 1 and 200")
            principal, service = await task_context(request, "*", "read")
            if not hasattr(service, "list_dead_letters"):
                raise HTTPException(503, "dead letter management is not configured")
            try:
                items = await service.list_dead_letters(
                    tenant_id=principal.tenant_id, limit=limit,
                    include_resolved=include_resolved,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            return {"items": [dict(item) for item in items]}

        @router.post("/dead-letters/{run_id}/retry")
        async def retry_dead_letter(run_id: str, request: Request):
            principal = await resolve_principal(request)
            service = await resolve_task_service(principal)
            if not hasattr(service, "get_dead_letter"):
                raise HTTPException(503, "dead letter management is not configured")
            row = await service.get_dead_letter(
                tenant_id=principal.tenant_id, run_id=run_id,
            )
            if row is None:
                raise HTTPException(404, "dead letter not found")
            if row.get("resolved_at"):
                raise HTTPException(409, "dead letter already resolved")
            task_id = row.get("task_id")
            if not task_id:
                raise HTTPException(409, "dead letter is not linked to a task")
            if not await authorize_task(principal, str(task_id), "retry"):
                raise HTTPException(403, "task retry denied")
            try:
                body = await request.json()
            except Exception:
                body = {}
            key = body.get("idempotency_key") if isinstance(body, Mapping) else None
            if not isinstance(key, str) or not key.strip():
                key = f"dlq-retry:{run_id}:{uuid4().hex}"
            try:
                result = await service.retry_dead_letter(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    actor_id=principal.user_id, idempotency_key=key,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            if result is None:
                raise HTTPException(404, "dead letter not found")
            return JSONResponse(dict(result), status_code=202)

        @router.post("/dead-letters/{run_id}/resolve")
        async def resolve_dead_letter(run_id: str, request: Request):
            principal = await resolve_principal(request)
            service = await resolve_task_service(principal)
            if not hasattr(service, "resolve_dead_letter"):
                raise HTTPException(503, "dead letter management is not configured")
            row = await service.get_dead_letter(
                tenant_id=principal.tenant_id, run_id=run_id,
            )
            if row is None:
                raise HTTPException(404, "dead letter not found")
            target = str(row.get("task_id") or "*")
            if not await authorize_task(principal, target, "retry"):
                raise HTTPException(403, "task retry denied")
            result = await service.resolve_dead_letter(
                tenant_id=principal.tenant_id, run_id=run_id,
                actor_id=principal.user_id,
            )
            if result is None:
                raise HTTPException(404, "dead letter not found")
            return result

        @router.post("/tasks/{task_id}/cancel")
        async def cancel_task(task_id: str, request: Request):
            principal, service = await task_context(request, task_id, "cancel")
            try:
                result = await service.cancel_task(
                    tenant_id=principal.tenant_id, task_id=task_id,
                    actor_id=principal.user_id,
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            if result is None:
                raise HTTPException(404, "task not found")
            return dict(result)

    return router
