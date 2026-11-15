"""Authenticated external data connector administration API."""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.control_plane.data_connectors import ConnectorError, DataConnectorService
from agent_platform.identity.models import Principal

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
ServiceResolver = Callable[[Principal], Awaitable[DataConnectorService]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]


def create_connectors_router(
    *, resolve_principal: PrincipalResolver,
    resolve_service: ServiceResolver,
    authorize: Authorizer,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/connectors", tags=["connectors"])

    async def context(request: Request, connection_id: str, action: str):
        principal = await resolve_principal(request)
        if not await authorize(principal, "connector", connection_id, action):
            raise HTTPException(403, "connector access denied")
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "connector repository tenant mismatch")
        return principal, service

    @router.post("", status_code=201)
    async def create_connection(request: Request):
        principal, service = await context(request, "*", "create")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        scopes = body.get("scopes", [])
        if not isinstance(scopes, list) or any(not isinstance(value, str) for value in scopes):
            raise HTTPException(400, "scopes must be an array of strings")
        try:
            connection = await service.create_connection(
                tenant_id=principal.tenant_id,
                connection_id=str(body.get("connection_id") or ""),
                provider=str(body.get("provider") or ""),
                display_name=str(body.get("display_name") or ""),
                secret_ref=str(body.get("secret_ref") or ""),
                scopes=tuple(scopes), actor_id=principal.user_id,
            )
        except ConnectorError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "connection_id": connection.connection_id,
            "provider": connection.provider,
            "display_name": connection.display_name,
            "scopes": list(connection.scopes), "status": connection.status,
        }

    @router.post("/{connection_id}/revoke")
    async def revoke_connection(connection_id: str, request: Request):
        principal, service = await context(request, connection_id, "revoke")
        try:
            revoked = await service.revoke(
                tenant_id=principal.tenant_id, connection_id=connection_id,
                revocation_disposition="retain",
            )
        except ConnectorError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not revoked:
            raise HTTPException(404, "connection not found or already revoked")
        return {"connection_id": connection_id, "status": "revoked"}

    return router
