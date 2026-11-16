"""Authenticated registry API; tenant and actor identity come from the host."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.control_plane.registry import (
    RegistryConflict,
    RegistryError,
    RegistryNotFound,
    RegistryService,
)
from agent_platform.identity.models import Principal

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
ServiceResolver = Callable[[Principal], Awaitable[RegistryService]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]


def create_registry_router(
    *, resolve_principal: PrincipalResolver,
    resolve_service: ServiceResolver,
    authorize: Authorizer,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/registry", tags=["registry"])

    async def context(request: Request, kind: str, item_id: str, action: str):
        principal = await resolve_principal(request)
        if not await authorize(principal, kind, item_id, action):
            raise HTTPException(403, "registry resource access denied")
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "registry repository tenant mismatch")
        return principal, service

    def serialize(item: Any) -> dict[str, Any]:
        return {
            "kind": item.kind, "item_id": item.item_id, "version": item.version,
            "definition": dict(item.definition), "status": item.status,
            "created_by": item.created_by, "created_at": item.created_at.isoformat(),
            "published_at": item.published_at.isoformat() if item.published_at else None,
        }

    @router.post("/{kind}/{item_id}/versions", status_code=201)
    async def create_version(kind: str, item_id: str, request: Request):
        principal, service = await context(request, kind, item_id, "write")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict) or not isinstance(body.get("definition"), dict):
            raise HTTPException(400, "definition object is required")
        try:
            item = await service.create_draft(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id,
                definition=body["definition"], actor_id=principal.user_id,
            )
        except RegistryError as exc:
            raise HTTPException(422, str(exc)) from exc
        return serialize(item)

    @router.post("/{kind}/{item_id}/versions/{version}/publish")
    async def publish(kind: str, item_id: str, version: int, request: Request):
        principal, service = await context(request, kind, item_id, "publish")
        try:
            return serialize(await service.publish(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id, version=version,
            ))
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (RegistryError, RegistryConflict) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/{kind}/{item_id}/rollback/{target_version}")
    async def rollback(kind: str, item_id: str, target_version: int, request: Request):
        principal, service = await context(request, kind, item_id, "publish")
        try:
            return serialize(await service.rollback(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id,
                target_version=target_version,
            ))
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (RegistryError, RegistryConflict) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{kind}/{item_id}/versions")
    async def list_versions(kind: str, item_id: str, request: Request):
        principal, service = await context(request, kind, item_id, "read")
        versions = await service.repository.list_versions(
            principal.tenant_id, kind, item_id,
        )
        return {"items": [serialize(item) for item in versions]}

    @router.post("/{kind}/{item_id}/disable")
    async def disable(kind: str, item_id: str, request: Request):
        principal, service = await context(request, kind, item_id, "publish")
        try:
            return serialize(await service.disable(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id,
                actor_id=principal.user_id,
            ))
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (RegistryError, RegistryConflict) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{kind}/{item_id}/active")
    async def get_active(kind: str, item_id: str, request: Request):
        principal, service = await context(request, kind, item_id, "read")
        try:
            return serialize(await service.resolve_active(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id,
            ))
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    return router
