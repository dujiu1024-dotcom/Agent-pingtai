"""Authenticated external data connector administration API."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

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
    audit_writer: Any | None = None,
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
        connection = await service.repository.get(principal.tenant_id, connection_id) if connection_id != "*" else None
        if connection_id != "*" and connection is None:
            raise HTTPException(404, "connector not found")
        if connection is not None and connection.project_id and not await authorize(
            principal, "project", connection.project_id, action,
        ):
            raise HTTPException(403, "linked project connector access denied")
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
        project_id = str(body["project_id"]) if body.get("project_id") else None
        if project_id and not await authorize(principal, "project", project_id, "connector.create"):
            raise HTTPException(403, "project connector creation denied")
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
                scopes=tuple(scopes), actor_id=principal.user_id, project_id=project_id,
            )
        except ConnectorError as exc:
            raise HTTPException(422, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="connector.created", subject_id=connection.connection_id,
                detail={"project_id": connection.project_id, "provider": connection.provider},
            )
        return {
            "connection_id": connection.connection_id,
            "provider": connection.provider,
            "display_name": connection.display_name,
            "scopes": list(connection.scopes), "status": connection.status,
            "project_id": connection.project_id,
        }

    @router.post("/{connection_id}/sync", status_code=202)
    async def request_sync(connection_id: str, request: Request):
        principal, service = await context(request, connection_id, "sync")
        try:
            await service.request_sync(
                tenant_id=principal.tenant_id, connection_id=connection_id,
            )
        except ConnectorError as exc:
            status = 503 if "queue is not configured" in str(exc) else 409
            raise HTTPException(status, str(exc)) from exc
        return {"connection_id": connection_id, "status": "queued"}

    @router.post("/{connection_id}/revoke")
    async def revoke_connection(
        connection_id: str, request: Request, disposition: str = "retain",
    ):
        principal, service = await context(request, connection_id, "revoke")
        if disposition not in {"retain", "delete"}:
            raise HTTPException(422, "disposition must be retain or delete")
        try:
            revoked = await service.revoke(
                tenant_id=principal.tenant_id, connection_id=connection_id,
                revocation_disposition=disposition,
            )
        except ConnectorError as exc:
            raise HTTPException(422, str(exc)) from exc
        if not revoked:
            raise HTTPException(404, "connection not found or already revoked")
        if audit_writer is not None:
            connection = await service.repository.get(principal.tenant_id, connection_id)
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="connector.revoked", subject_id=connection_id,
                detail={
                    "project_id": connection.project_id if connection else None,
                    "disposition": disposition,
                },
            )
        return {
            "connection_id": connection_id, "status": "revoked",
            "disposition": disposition,
        }

    return router
