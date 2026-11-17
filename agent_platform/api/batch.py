"""Tenant-scoped Batch Execution API for the platform workbench."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from agent_platform.identity.models import Principal


class BatchTaskService(Protocol):
    """Host-owned batch service; implementations persist jobs and dispatch BatchExecutor."""

    async def submit_batch(
        self, *, tenant_id: str, actor_id: str, template_id: str,
        dataset: Sequence[Mapping[str, Any]], max_concurrency: int,
        idempotency_key: str | None = None,
    ) -> Mapping[str, Any]: ...

    async def list_batches(
        self, *, tenant_id: str, actor_id: str, limit: int = 100,
    ) -> Sequence[Mapping[str, Any]]: ...

    async def get_batch(
        self, *, tenant_id: str, batch_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def cancel_batch(
        self, *, tenant_id: str, batch_id: str, actor_id: str,
    ) -> Mapping[str, Any] | None: ...

    async def retry_batch(
        self, *, tenant_id: str, batch_id: str, actor_id: str,
        idempotency_key: str,
    ) -> Mapping[str, Any] | None: ...


PrincipalResolver = Callable[[Request], Awaitable[Principal]]
BatchServiceResolver = Callable[[Principal], Awaitable[BatchTaskService]]
BatchAuthorizer = Callable[[Principal, str, str], Awaitable[bool]]


def create_batch_router(
    *,
    resolve_principal: PrincipalResolver,
    resolve_service: BatchServiceResolver,
    authorize: BatchAuthorizer,
) -> APIRouter:
    """Mount submit/query/cancel/retry under the authenticated workbench API."""
    router = APIRouter(prefix="/api/platform/v1/workbench/batches", tags=["batch-execution"])

    async def context(request: Request, batch_id: str, action: str):
        principal = await resolve_principal(request)
        if not await authorize(principal, batch_id, action):
            raise HTTPException(403, "batch access denied")
        service = await resolve_service(principal)
        bound_tenant = getattr(
            service, "tenant_id",
            getattr(getattr(service, "repository", None), "tenant_id", principal.tenant_id),
        )
        if bound_tenant != principal.tenant_id:
            raise HTTPException(403, "batch service tenant mismatch")
        return principal, service

    @router.get("")
    async def list_batches(request: Request, limit: int = 100):
        if not 1 <= limit <= 200:
            raise HTTPException(400, "limit must be between 1 and 200")
        principal, service = await context(request, "*", "read")
        try:
            items = await service.list_batches(
                tenant_id=principal.tenant_id, actor_id=principal.user_id, limit=limit,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        return {"items": [dict(item) for item in items]}

    @router.post("", status_code=202)
    async def submit_batch(request: Request):
        principal, service = await context(request, "*", "create")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        template_id = body.get("template_id")
        dataset = body.get("dataset")
        concurrency = body.get("max_concurrency", 8)
        if not isinstance(template_id, str) or not template_id.strip():
            raise HTTPException(422, "template_id is required")
        if (
            not isinstance(dataset, list) or not dataset
            or any(not isinstance(row, dict) for row in dataset)
        ):
            raise HTTPException(422, "dataset must be a non-empty array of input objects")
        if len(dataset) > 10000:
            raise HTTPException(422, "dataset cannot exceed 10000 rows")
        if type(concurrency) is not int or not 1 <= concurrency <= 1000:
            raise HTTPException(422, "max_concurrency must be an integer between 1 and 1000")
        key = body.get("idempotency_key")
        if key is not None and (not isinstance(key, str) or not key.strip()):
            raise HTTPException(422, "idempotency_key must be a non-empty string")
        if not await authorize(principal, template_id, "execute"):
            raise HTTPException(403, "template execution access denied")
        try:
            result = await service.submit_batch(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                template_id=template_id, dataset=dataset,
                max_concurrency=concurrency, idempotency_key=key,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return JSONResponse(dict(result), status_code=202)

    @router.get("/{batch_id}")
    async def get_batch(batch_id: str, request: Request):
        principal, service = await context(request, batch_id, "read")
        result = await service.get_batch(
            tenant_id=principal.tenant_id, batch_id=batch_id, actor_id=principal.user_id,
        )
        if result is None:
            raise HTTPException(404, "batch not found")
        return dict(result)

    @router.post("/{batch_id}/cancel")
    async def cancel_batch(batch_id: str, request: Request):
        principal, service = await context(request, batch_id, "cancel")
        try:
            result = await service.cancel_batch(
                tenant_id=principal.tenant_id, batch_id=batch_id, actor_id=principal.user_id,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if result is None:
            raise HTTPException(404, "batch not found")
        return dict(result)

    @router.post("/{batch_id}/retry", status_code=202)
    async def retry_batch(batch_id: str, request: Request):
        principal, service = await context(request, batch_id, "retry")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "retry request body must be JSON") from exc
        key = body.get("idempotency_key") if isinstance(body, Mapping) else None
        if not isinstance(key, str) or not key.strip():
            raise HTTPException(422, "idempotency_key is required for batch retry")
        try:
            result = await service.retry_batch(
                tenant_id=principal.tenant_id, batch_id=batch_id,
                actor_id=principal.user_id, idempotency_key=key,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if result is None:
            raise HTTPException(404, "batch not found")
        return JSONResponse(dict(result), status_code=202)

    return router
