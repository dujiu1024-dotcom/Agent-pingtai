"""Authenticated session/message/file API backed by SessionAssetService."""
from __future__ import annotations

from typing import Any, Awaitable, Callable
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from agent_platform.control_plane.sessions_assets import (
    MessageRole,
    SessionAssetService,
    SessionError,
    SessionNotFound,
)
from agent_platform.identity.models import Principal
from agent_platform.memory.extraction import MemoryExtractionService, MemoryTurn
from agent_platform.memory.service import MemoryPrincipal

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
ServiceResolver = Callable[[Principal], Awaitable[SessionAssetService]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]
MemoryPrincipalResolver = Callable[[Request], Awaitable[MemoryPrincipal]]


def create_sessions_router(
    *, resolve_principal: PrincipalResolver,
    resolve_service: ServiceResolver,
    authorize: Authorizer,
    memory_extraction: MemoryExtractionService | None = None,
    resolve_memory_principal: MemoryPrincipalResolver | None = None,
    audit_writer: Any | None = None,
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
        # Session ACL alone is insufficient for project-bound sessions: a
        # stale or overly broad session grant must not bypass project ACL.
        if resource_type == "session":
            session = await service.repository.get_session(
                principal.tenant_id, resource_id,
            )
            if session is None:
                raise HTTPException(404, "session not found")
            if session.project_id and not await authorize(
                principal, "project", session.project_id, action,
            ):
                raise HTTPException(403, "linked project session access denied")
        if audit_writer is not None:
            trace = getattr(request.state, "trace_context", None)
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="resource.access_granted", subject_id=resource_id,
                detail={"resource_type": resource_type, "requested_action": action,
                        "trace_id": getattr(trace, "trace_id", None),
                        "correlation_id": getattr(trace, "correlation_id", None)},
            )
        return principal, service

    @router.get("/sessions")
    async def list_sessions(request: Request, limit: int = 100, project_id: str | None = None):
        principal = await resolve_principal(request)
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "session repository tenant mismatch")
        if project_id and not await authorize(principal, "project", project_id, "read"):
            raise HTTPException(403, "project session access denied")
        try:
            items = await service.repository.list_sessions(
                principal.tenant_id, principal.user_id, limit, project_id,
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        visible = []
        for session in items:
            if not await authorize(principal, "session", session.session_id, "read"):
                continue
            if session.project_id and not await authorize(
                principal, "project", session.project_id, "read",
            ):
                continue
            visible.append({
                "session_id": session.session_id, "title": session.title,
                "project_id": session.project_id, "status": session.status,
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
            })
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="session.list", subject_id=principal.user_id,
                detail={"project_id": project_id, "returned": len(visible)},
            )
        return {"items": visible}

    @router.get("/files")
    async def list_files(request: Request, session_id: str | None = None, limit: int = 100, project_id: str | None = None):
        principal = await resolve_principal(request)
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "session repository tenant mismatch")
        if session_id and not await authorize(principal, "session", session_id, "read"):
            raise HTTPException(403, "session file access denied")
        if project_id and not await authorize(principal, "project", project_id, "read"):
            raise HTTPException(403, "project file access denied")
        try:
            items = await service.repository.list_assets(
                principal.tenant_id, principal.user_id, session_id, limit, project_id,
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        visible = []
        for asset in items:
            if not await authorize(principal, "file", asset.asset_id, "read"):
                continue
            if asset.session_id and not await authorize(
                principal, "session", asset.session_id, "read",
            ):
                continue
            if asset.project_id and not await authorize(
                principal, "project", asset.project_id, "read",
            ):
                continue
            visible.append({
                    "asset_id": asset.asset_id, "filename": asset.filename,
                    "media_type": asset.media_type, "size_bytes": asset.size_bytes,
                    "status": asset.status.value, "session_id": asset.session_id,
                    "project_id": asset.project_id, "version": asset.version,
                    "created_at": asset.created_at.isoformat(),
                })
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.list", subject_id=principal.user_id,
                detail={"project_id": project_id, "session_id": session_id,
                        "returned": len(visible)},
            )
        return {"items": visible}

    @router.post("/sessions", status_code=201)
    async def create_session(request: Request):
        principal = await resolve_principal(request)
        if not await authorize(principal, "session", "*", "create"):
            raise HTTPException(403, "session creation denied")
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "session repository tenant mismatch")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        if body.get("project_id") and not await authorize(
            principal, "project", str(body["project_id"]), "session.create",
        ):
            raise HTTPException(403, "project session creation denied")
        try:
            session = await service.create_session(
                tenant_id=principal.tenant_id, owner_id=principal.user_id,
                title=str(body.get("title") or ""),
                project_id=str(body["project_id"]) if body.get("project_id") else None,
            )
        except SessionError as exc:
            raise HTTPException(422, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="session.created", subject_id=session.session_id,
                detail={"project_id": session.project_id},
            )
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
            if role is not MessageRole.USER:
                raise ValueError("human session endpoint only accepts user messages")
            assets = body.get("asset_ids", [])
            if not isinstance(assets, list) or any(not isinstance(v, str) for v in assets):
                raise ValueError("asset_ids must be an array of strings")
            for asset_id in assets:
                if not await authorize(principal, "file", asset_id, "read"):
                    raise HTTPException(403, "referenced file access denied")
            message = await service.append_message(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                session_id=session_id, role=role, content=str(body.get("content") or ""),
                asset_ids=tuple(assets), metadata=body.get("metadata") or {},
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(404 if isinstance(exc, SessionNotFound) else 422, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="message.appended", subject_id=message.message_id,
                detail={"session_id": session_id, "role": message.role.value},
            )
        return {"message_id": message.message_id, "session_id": message.session_id}

    if memory_extraction is not None and resolve_memory_principal is not None:
        @router.post("/sessions/{session_id}/memory-extract", status_code=202)
        async def extract_session_memory(session_id: str, request: Request):
            principal, service = await context(request, "session", session_id, "write")
            memory_principal = await resolve_memory_principal(request)
            if (
                memory_principal.tenant_id != principal.tenant_id
                or memory_principal.user_id != principal.user_id
            ):
                raise HTTPException(403, "memory principal does not match session principal")
            try:
                session = await service.repository.get_session(
                    principal.tenant_id, session_id,
                )
                if session is None:
                    raise HTTPException(404, "session not found")
                if session.owner_id != principal.user_id:
                    raise HTTPException(
                        403, "memory extraction is restricted to the session owner",
                    )
                messages = await service.repository.list_messages(
                    principal.tenant_id, session_id, limit=50,
                )
            except (SessionError, SessionNotFound) as exc:
                raise HTTPException(404, str(exc)) from exc

            assistant_index = next(
                (index for index in range(len(messages) - 1, -1, -1)
                 if messages[index].role is MessageRole.ASSISTANT),
                None,
            )
            if assistant_index is None:
                raise HTTPException(409, "session has no completed assistant turn")
            user_message = next(
                (messages[index] for index in range(assistant_index - 1, -1, -1)
                 if messages[index].role is MessageRole.USER),
                None,
            )
            if user_message is None:
                raise HTTPException(409, "assistant turn has no preceding user message")
            assistant_message = messages[assistant_index]
            metadata = dict(assistant_message.metadata or {})
            agent_id = metadata.get("agent_id")
            if agent_id is not None and not isinstance(agent_id, str):
                raise HTTPException(400, "assistant agent_id metadata must be a string")
            turn = MemoryTurn(
                tenant_id=principal.tenant_id,
                user_id=principal.user_id,
                source_ref=(
                    f"session:{session_id}:user:{user_message.message_id}:"
                    f"assistant:{assistant_message.message_id}"
                ),
                user_message=user_message.content,
                assistant_message=assistant_message.content,
                agent_id=agent_id,
                project_id=session.project_id,
            )
            try:
                existing_records = await memory_extraction.memory.repository.list(
                    principal.tenant_id, limit=5000,
                )
                records = [
                    record for record in existing_records
                    if record.candidate.source_ref == turn.source_ref
                    and record.status.value != "deleted"
                ]
                if not records:
                    records = await memory_extraction.propose_from_turn(
                        principal=memory_principal, turn=turn,
                    )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {
                "source_ref": turn.source_ref,
                "items": [{
                    "memory_id": record.candidate.memory_id,
                    "scope": record.candidate.scope.value,
                    "scope_id": record.candidate.scope_id,
                    "content": record.candidate.content,
                    "confidence": record.candidate.confidence,
                    "status": record.status.value,
                } for record in records],
            }

    @router.post("/files", status_code=201)
    async def upload_file(request: Request, session_id: str | None = None, project_id: str | None = None):
        principal = await resolve_principal(request)
        if not await authorize(principal, "file", "*", "create"):
            raise HTTPException(403, "file upload denied")
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "session repository tenant mismatch")
        linked_session = None
        if session_id:
            if not await authorize(principal, "session", session_id, "write"):
                raise HTTPException(403, "session attachment denied")
            linked_session = await service.repository.get_session(
                principal.tenant_id, session_id,
            )
            if linked_session is None:
                raise HTTPException(404, "session not found")
        # A project inherited from the linked session is still a project-scoped
        # write. Do not let session-write permission bypass project file.create.
        effective_project_id = project_id or (
            linked_session.project_id if linked_session is not None else None
        )
        if effective_project_id and not await authorize(
            principal, "project", effective_project_id, "file.create",
        ):
            raise HTTPException(403, "project file upload denied")
        filename = request.headers.get("x-filename", "upload.bin")
        media_type = request.headers.get("content-type", "application/octet-stream").split(";", 1)[0]
        max_bytes = int(getattr(service, "max_upload_bytes", 25 * 1024 * 1024))
        declared_length = request.headers.get("content-length")
        if declared_length:
            if not declared_length.isdigit():
                raise HTTPException(400, "invalid content-length")
            if int(declared_length) > max_bytes:
                raise HTTPException(413, "file exceeds upload size limit")
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise HTTPException(413, "file exceeds upload size limit")
        content = bytes(chunks)
        try:
            asset = await service.upload_asset(
                tenant_id=principal.tenant_id, owner_id=principal.user_id,
                filename=filename, media_type=media_type, content=content,
                session_id=session_id, project_id=project_id,
            )
        except (SessionError, SessionNotFound) as exc:
            raise HTTPException(422, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.uploaded", subject_id=asset.asset_id,
                detail={"project_id": asset.project_id, "session_id": asset.session_id,
                        "size_bytes": asset.size_bytes, "sha256": asset.sha256},
            )
        return {
            "asset_id": asset.asset_id, "filename": asset.filename,
            "media_type": asset.media_type, "size_bytes": asset.size_bytes,
            "sha256": asset.sha256, "status": asset.status.value,
            "session_id": asset.session_id, "version": asset.version,
        }

    @router.post("/files/{asset_id}/versions", status_code=201)
    async def create_file_version(asset_id: str, request: Request, expected_version: int | None = None):
        principal, service = await context(request, "file", asset_id, "write")
        asset = await service.repository.get_asset(principal.tenant_id, asset_id)
        if asset is None:
            raise HTTPException(404, "file not found")
        if asset.session_id and not await authorize(
            principal, "session", asset.session_id, "write",
        ):
            raise HTTPException(403, "linked session modification denied")
        if asset.project_id and not await authorize(
            principal, "project", asset.project_id, "file.update",
        ):
            raise HTTPException(403, "linked project file update denied")
        max_bytes = int(getattr(service, "max_upload_bytes", 25 * 1024 * 1024))
        declared_length = request.headers.get("content-length")
        if declared_length:
            if not declared_length.isdigit():
                raise HTTPException(400, "invalid content-length")
            if int(declared_length) > max_bytes:
                raise HTTPException(413, "file exceeds upload size limit")
        chunks = bytearray()
        async for chunk in request.stream():
            chunks.extend(chunk)
            if len(chunks) > max_bytes:
                raise HTTPException(413, "file exceeds upload size limit")
        try:
            version = await service.create_asset_version(
                tenant_id=principal.tenant_id, asset_id=asset_id,
                content=bytes(chunks), filename=request.headers.get("x-filename"),
                media_type=request.headers.get("content-type", asset.media_type).split(";", 1)[0],
                expected_version=expected_version,
            )
        except SessionNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.version_created", subject_id=asset_id,
                detail={"project_id": asset.project_id, "session_id": asset.session_id,
                        "version": version.version, "sha256": version.sha256,
                        "size_bytes": version.size_bytes},
            )
        return {
            "asset_id": version.asset_id, "version": version.version,
            "filename": version.filename, "media_type": version.media_type,
            "size_bytes": version.size_bytes, "sha256": version.sha256,
            "status": version.status.value, "created_at": version.created_at.isoformat(),
        }

    @router.get("/files/{asset_id}/versions")
    async def list_file_versions(asset_id: str, request: Request):
        principal, service = await context(request, "file", asset_id, "read")
        asset = await service.repository.get_asset(principal.tenant_id, asset_id)
        if asset is None:
            raise HTTPException(404, "file not found")
        if asset.session_id and not await authorize(
            principal, "session", asset.session_id, "read",
        ):
            raise HTTPException(403, "linked session access denied")
        if asset.project_id and not await authorize(
            principal, "project", asset.project_id, "read",
        ):
            raise HTTPException(403, "linked project access denied")
        versions = await service.list_asset_versions(
            tenant_id=principal.tenant_id, asset_id=asset_id,
        )
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.versions_listed", subject_id=asset_id,
                detail={"project_id": asset.project_id, "session_id": asset.session_id,
                        "current_version": asset.version, "returned": len(versions)},
            )
        return {"asset_id": asset_id, "current_version": asset.version, "items": [{
            "version": item.version, "filename": item.filename,
            "media_type": item.media_type, "size_bytes": item.size_bytes,
            "sha256": item.sha256, "status": item.status.value,
            "created_at": item.created_at.isoformat(),
        } for item in versions]}

    @router.get("/files/{asset_id}/versions/{version}")
    async def download_file_version(asset_id: str, version: int, request: Request):
        principal, service = await context(request, "file", asset_id, "read")
        asset = await service.repository.get_asset(principal.tenant_id, asset_id)
        if asset is None:
            raise HTTPException(404, "file not found")
        if asset.session_id and not await authorize(
            principal, "session", asset.session_id, "read",
        ):
            raise HTTPException(403, "linked session access denied")
        if asset.project_id and not await authorize(
            principal, "project", asset.project_id, "read",
        ):
            raise HTTPException(403, "linked project access denied")
        try:
            record, content = await service.download_asset_version(
                tenant_id=principal.tenant_id, asset_id=asset_id, version=version,
            )
        except SessionNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.version_downloaded", subject_id=asset_id,
                detail={"project_id": asset.project_id, "session_id": asset.session_id,
                        "version": record.version, "size_bytes": len(content),
                        "sha256": record.sha256},
            )
        return Response(
            content, media_type=record.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{quote(record.filename)}"',
                "X-File-Asset-Version": str(record.version),
                "Cache-Control": "no-store",
            },
        )

    @router.post("/files/{asset_id}/versions/{version}/restore", status_code=201)
    async def restore_file_version(
        asset_id: str, version: int, request: Request, expected_version: int | None = None,
    ):
        principal, service = await context(request, "file", asset_id, "write")
        asset = await service.repository.get_asset(principal.tenant_id, asset_id)
        if asset is None:
            raise HTTPException(404, "file not found")
        if asset.session_id and not await authorize(
            principal, "session", asset.session_id, "write",
        ):
            raise HTTPException(403, "linked session modification denied")
        if asset.project_id and not await authorize(
            principal, "project", asset.project_id, "file.update",
        ):
            raise HTTPException(403, "linked project file update denied")
        try:
            restored = await service.restore_asset_version(
                tenant_id=principal.tenant_id, asset_id=asset_id,
                version=version, expected_version=expected_version,
            )
        except SessionNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.version_restored", subject_id=asset_id,
                detail={"project_id": asset.project_id, "session_id": asset.session_id,
                        "restored_from_version": version, "version": restored.version,
                        "sha256": restored.sha256, "size_bytes": restored.size_bytes},
            )
        return {
            "asset_id": restored.asset_id, "version": restored.version,
            "restored_from_version": version, "filename": restored.filename,
            "media_type": restored.media_type, "size_bytes": restored.size_bytes,
            "sha256": restored.sha256, "status": restored.status.value,
            "created_at": restored.created_at.isoformat(),
        }

    @router.delete("/files/{asset_id}", status_code=204)
    async def delete_file(asset_id: str, request: Request):
        principal, service = await context(request, "file", asset_id, "delete")
        asset = await service.repository.get_asset(principal.tenant_id, asset_id)
        if asset is None:
            raise HTTPException(404, "file not found")
        if asset.session_id and not await authorize(
            principal, "session", asset.session_id, "write",
        ):
            raise HTTPException(403, "linked session modification denied")
        if asset.project_id and not await authorize(
            principal, "project", asset.project_id, "file.delete",
        ):
            raise HTTPException(403, "linked project file deletion denied")
        try:
            await service.delete_asset(tenant_id=principal.tenant_id, asset_id=asset_id)
        except SessionNotFound as exc:
            raise HTTPException(404, "file not found") from exc
        except SessionError as exc:
            raise HTTPException(503, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.deleted", subject_id=asset_id,
                detail={"project_id": asset.project_id, "session_id": asset.session_id},
            )
        return Response(status_code=204)

    @router.get("/files/{asset_id}/metadata")
    async def get_file_metadata(asset_id: str, request: Request):
        principal, service = await context(request, "file", asset_id, "read")
        asset = await service.repository.get_asset(principal.tenant_id, asset_id)
        if asset is None:
            raise HTTPException(404, "file not found")
        if asset.session_id and not await authorize(
            principal, "session", asset.session_id, "read",
        ):
            raise HTTPException(403, "linked session access denied")
        if asset.project_id and not await authorize(
            principal, "project", asset.project_id, "read",
        ):
            raise HTTPException(403, "linked project access denied")
        return {
            "asset_id": asset.asset_id, "filename": asset.filename,
            "media_type": asset.media_type, "size_bytes": asset.size_bytes,
            "sha256": asset.sha256, "status": asset.status.value,
            "session_id": asset.session_id, "project_id": asset.project_id,
            "version": asset.version, "created_at": asset.created_at.isoformat(),
        }

    @router.get("/files/{asset_id}/download-url")
    async def create_download_url(asset_id: str, request: Request, expires_in: int = 300):
        principal, service = await context(request, "file", asset_id, "read")
        asset = await service.repository.get_asset(principal.tenant_id, asset_id)
        if asset is None:
            raise HTTPException(404, "file not found")
        if asset.session_id and not await authorize(
            principal, "session", asset.session_id, "read",
        ):
            raise HTTPException(403, "linked session access denied")
        if asset.project_id and not await authorize(
            principal, "project", asset.project_id, "read",
        ):
            raise HTTPException(403, "linked project access denied")
        try:
            asset, url = await service.create_download_url(
                tenant_id=principal.tenant_id, asset_id=asset_id, expires_in=expires_in,
            )
        except SessionNotFound as exc:
            raise HTTPException(404, "file not found") from exc
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.download_url_issued", subject_id=asset_id,
                detail={"project_id": asset.project_id, "expires_in": expires_in},
            )
        return JSONResponse(
            {"asset_id": asset.asset_id, "url": url, "expires_in": expires_in},
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )

    @router.get("/files/{asset_id}")
    async def download_file(asset_id: str, request: Request):
        principal, service = await context(request, "file", asset_id, "read")
        asset = await service.repository.get_asset(principal.tenant_id, asset_id)
        if asset is None:
            raise HTTPException(404, "file not found")
        if asset.session_id and not await authorize(
            principal, "session", asset.session_id, "read",
        ):
            raise HTTPException(403, "linked session access denied")
        if asset.project_id and not await authorize(
            principal, "project", asset.project_id, "read",
        ):
            raise HTTPException(403, "linked project access denied")
        try:
            asset, content = await service.download_asset(
                tenant_id=principal.tenant_id, asset_id=asset_id,
            )
        except SessionNotFound as exc:
            raise HTTPException(404, "file not found") from exc
        except SessionError as exc:
            raise HTTPException(409, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="file.downloaded", subject_id=asset_id,
                detail={"project_id": asset.project_id, "size_bytes": len(content)},
            )
        headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(asset.filename)}"}
        return Response(content=content, media_type=asset.media_type, headers=headers)

    return router
