"""Authenticated session/message/file API backed by SessionAssetService."""
from __future__ import annotations

from typing import Any, Awaitable, Callable
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from agent_platform.control_plane.sessions_assets import (
    MessageRole, SessionAssetService, SessionError, SessionNotFound,
)
from agent_platform.identity.models import Principal

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
ServiceResolver = Callable[[Principal], Awaitable[SessionAssetService]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]


def create_sessions_router(
    *, resolve_principal: PrincipalResolver,
    resolve_service: ServiceResolver,
    authorize: Authorizer,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1", tags=["sessions"])

    async def context(request: Request, resource_type: str, resource_id: str, action: str):
        principal = await resolve_principal(request)
        if not await authorize(principal, resource_type, resource_id, action):
            raise HTTPException(403, "session or file access denied")
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "session repository tenant mismatch")
        return principal, service

    @router.post("/sessions", status_code=201)
    async def create_session(request: Request):
        principal = await resolve_principal(request)
        if not await authorize(principal, "session", "*", "create"):
            raise HTTPException(403, "session creation denied")
        service = await resolve_service(principal)
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        try:
            session = await service.create_session(
                tenant_id=principal.tenant_id, owner_id=principal.user_id,
                title=str(body.get("title") or ""),
                project_id=str(body["project_id"]) if body.get("project_id") else None,
            )
        except SessionError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "session_id": session.session_id, "title": session.title,
            "project_id": session.project_id, "status": session.status,
            "created_at": session.created_at.isoformat(),
        }

    @router.get("/sessions/{session_id}/messages")
    async def list_messages(session_id: str, request: Request, limit: int = 100):
        principal, service = await context(request, "session", session_id, "read")
        try:
            messages = await service.repository.list_messages(principal.tenant_id, session_id, limit)
        except (SessionError, ValueError) as exc:
            raise HTTPException(404 if isinstance(exc, SessionNotFound) else 400, str(exc)) from exc
        return {"items": [{
            "message_id": m.message_id, "role": m.role.value, "content": m.content,
            "actor_id": m.actor_id, "task_id": m.task_id, "run_id": m.run_id,
            "asset_ids": list(m.asset_ids), "metadata": dict(m.metadata),
            "created_at": m.created_at.isoformat(),
        } for m in messages]}

    @router.post("/sessions/{session_id}/messages", status_code=201)
    async def append_message(session_id: str, request: Request):
        principal, service = await context(request, "session", session_id, "write")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        try:
            role = MessageRole(body.get("role"))
            assets = body.get("asset_ids", [])
            if not isinstance(assets, list) or any(not isinstance(v, str) for v in assets):
                raise ValueError("asset_ids must be an array of strings")
            message = await service.append_message(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                session_id=session_id, role=role, content=str(body.get("content") or ""),
                run_id=str(body["run_id"]) if body.get("run_id") else None,
                task_id=str(body["task_id"]) if body.get("task_id") else None,
                asset_ids=tuple(assets), metadata=body.get("metadata") or {},
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(404 if isinstance(exc, SessionNotFound) else 422, str(exc)) from exc
        return {"message_id": message.message_id, "session_id": message.session_id}

    @router.post("/files", status_code=201)
    async def upload_file(request: Request, session_id: str | None = None, project_id: str | None = None):
        principal = await resolve_principal(request)
        if not await authorize(principal, "file", "*", "create"):
            raise HTTPException(403, "file upload denied")
        service = await resolve_service(principal)
        filename = request.headers.get("x-filename", "upload.bin")
        media_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        content = await request.body()
        try:
            asset = await service.upload_asset(
                tenant_id=principal.tenant_id, owner_id=principal.user_id,
                filename=filename, media_type=media_type, content=content,
                session_id=session_id, project_id=project_id,
            )
        except (SessionError, SessionNotFound) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "asset_id": asset.asset_id, "filename": asset.filename,
            "media_type": asset.media_type, "size_bytes": asset.size_bytes,
            "sha256": asset.sha256, "status": asset.status.value,
            "session_id": asset.session_id,
        }

    @router.get("/files/{asset_id}")
    async def download_file(asset_id: str, request: Request):
        principal, service = await context(request, "file", asset_id, "read")
        try:
            asset, content = await service.download_asset(
                tenant_id=principal.tenant_id, asset_id=asset_id,
            )
        except SessionNotFound as exc:
            raise HTTPException(404, "file not found") from exc
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(asset.filename)}"}
        return Response(content=content, media_type=asset.media_type, headers=headers)

    return router
