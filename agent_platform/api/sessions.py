"""Authenticated session/message/file API backed by SessionAssetService."""
from __future__ import annotations

import json
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
            if session is None or (session.status == "deleted" and action != "delete"):
                raise HTTPException(404, "session not found")
            if session.project_id and not await authorize(
                principal, "project", session.project_id, action,
            ):
                raise HTTPException(403, "linked project session access denied")
        if resource_type == "file":
            asset = await service.repository.get_asset(principal.tenant_id, resource_id)
            if asset is not None and asset.session_id:
                linked_session = await service.repository.get_session(
                    principal.tenant_id, asset.session_id,
                )
                if linked_session is None or linked_session.status == "deleted":
                    raise HTTPException(404, "linked session not found")
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
    async def list_sessions(
        request: Request, limit: int = 100, project_id: str | None = None,
        include_archived: bool = False,
    ):
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
            if not include_archived and session.status != "active":
                continue
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
    async def list_messages(
        session_id: str, request: Request, limit: int = 100,
        before_message_id: str | None = None,
    ):
        principal, service = await context(request, "session", session_id, "read")
        try:
            messages = await service.repository.list_messages(
                principal.tenant_id, session_id, limit, before_message_id,
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(404 if isinstance(exc, SessionNotFound) else 400, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="message.list", subject_id=session_id,
                detail={
                    "limit": limit, "before_message_id": before_message_id,
                    "returned": len(messages),
                },
            )
        return {
            "items": [{
                "message_id": m.message_id, "role": m.role.value, "content": m.content,
                "actor_id": m.actor_id, "task_id": m.task_id, "run_id": m.run_id,
                "asset_ids": list(m.asset_ids), "metadata": dict(m.metadata),
                "created_at": m.created_at.isoformat(),
            } for m in messages],
            "next_cursor": messages[0].message_id if len(messages) == limit else None,
        }

    @router.post("/sessions/{session_id}/messages", status_code=201)
    async def append_message(session_id: str, request: Request):
        principal, service = await context(request, "session", session_id, "write")
        session = await service.repository.get_session(principal.tenant_id, session_id)
        if session is None or session.status != "active":
            raise HTTPException(409, "messages can only be appended to active sessions")
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

            # Only the newest persisted message can close the turn. If a newer
            # user message or partial interaction exists, never extract an older turn.
            if not messages or messages[-1].role is not MessageRole.ASSISTANT:
                raise HTTPException(
                    409, "memory extraction requires a completed assistant turn at the end of the session",
                )
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
                # The assistant message is the canonical turn-completion anchor.
                source_ref=f"session://{principal.tenant_id}/{assistant_message.message_id}@1",
                user_message=user_message.content,
                assistant_message=assistant_message.content,
                agent_id=agent_id,
                project_id=session.project_id,
                completed=True,
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
            if linked_session is None or linked_session.status == "deleted":
                raise HTTPException(404, "session not found")
            if linked_session.status != "active":
                raise HTTPException(409, "files can only be attached to active sessions")
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


    @router.get("/sessions/{session_id}")
    async def get_session(session_id: str, request: Request, message_limit: int = 100):
        principal, service = await context(request, "session", session_id, "read")
        session = await service.repository.get_session(principal.tenant_id, session_id)
        if session is None or session.status == "deleted":
            raise HTTPException(404, "session not found")
        try:
            messages = await service.repository.list_messages(
                principal.tenant_id, session_id, limit=message_limit,
            )
            snapshots = await service.repository.list_context_snapshots(
                principal.tenant_id, session_id, limit=20,
            )
        except (SessionError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="session.read", subject_id=session_id,
                detail={"message_count": len(messages), "snapshot_count": len(snapshots)},
            )
        return {
            "session": {
                "session_id": session.session_id, "title": session.title,
                "project_id": session.project_id, "agent_id": session.agent_id,
                "isolation_scope": session.isolation_scope.value,
                "status": session.status, "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
            },
            "messages": [{
                "message_id": m.message_id, "role": m.role.value, "content": m.content,
                "actor_id": m.actor_id, "task_id": m.task_id, "run_id": m.run_id,
                "asset_ids": list(m.asset_ids), "metadata": dict(m.metadata),
                "token_usage": (
                    {
                        "prompt_tokens": m.token_usage.prompt_tokens,
                        "completion_tokens": m.token_usage.completion_tokens,
                        "total_tokens": m.token_usage.total_tokens,
                        "model": m.token_usage.model,
                    } if m.token_usage is not None else None
                ),
                "created_at": m.created_at.isoformat(),
            } for m in messages],
            "context_snapshots": [{
                "snapshot_id": s.snapshot_id, "version": s.version,
                "run_id": s.run_id, "last_message_id": s.last_message_id,
                "token_count": s.token_count, "max_tokens": s.max_tokens,
                "created_by": s.created_by, "created_at": s.created_at.isoformat(),
            } for s in snapshots],
        }

    @router.get("/sessions/{session_id}/export")
    async def export_session(session_id: str, request: Request, format: str = "json"):
        if format not in {"json", "markdown"}:
            raise HTTPException(400, "format must be json or markdown")
        principal, service = await context(request, "session", session_id, "read")
        session = await service.repository.get_session(principal.tenant_id, session_id)
        if session is None or session.status == "deleted":
            raise HTTPException(404, "session not found")
        # Export every message in bounded pages; never silently truncate history.
        messages = []
        cursor = None
        for _ in range(21):
            page = await service.repository.list_messages(
                principal.tenant_id, session_id, limit=500,
                before_message_id=cursor,
            )
            if not page:
                break
            messages = list(page) + messages
            if len(messages) > 10000:
                raise HTTPException(413, "session export exceeds 10000 messages")
            if len(page) < 500:
                break
            cursor = page[0].message_id
        asset_ids = sorted({
            asset_id for message in messages for asset_id in message.asset_ids
        })
        exported_assets = []
        for asset_id in asset_ids:
            if not await authorize(principal, "file", asset_id, "read"):
                raise HTTPException(403, "referenced file access denied during export")
            asset = await service.repository.get_asset(principal.tenant_id, asset_id)
            if asset is None or asset.status.value == "deleted":
                exported_assets.append({"asset_id": asset_id, "status": "unavailable"})
                continue
            if asset.session_id and not await authorize(
                principal, "session", asset.session_id, "read",
            ):
                raise HTTPException(403, "linked session file access denied during export")
            if asset.project_id and not await authorize(
                principal, "project", asset.project_id, "read",
            ):
                raise HTTPException(403, "linked project file access denied during export")
            versions = await service.repository.list_asset_versions(
                principal.tenant_id, asset_id,
            )
            exported_assets.append({
                "asset_id": asset.asset_id, "filename": asset.filename,
                "media_type": asset.media_type, "size_bytes": asset.size_bytes,
                "sha256": asset.sha256, "current_version": asset.version,
                "status": asset.status.value,
                "versions": [{
                    "version": version.version, "filename": version.filename,
                    "media_type": version.media_type, "size_bytes": version.size_bytes,
                    "sha256": version.sha256, "status": version.status.value,
                    "created_at": version.created_at.isoformat(),
                } for version in versions],
            })
        payload = {
            "session": {
                "session_id": session.session_id, "title": session.title,
                "tenant_id": principal.tenant_id, "owner_id": session.owner_id,
                "project_id": session.project_id, "agent_id": session.agent_id,
                "isolation_scope": session.isolation_scope.value,
                "status": session.status, "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
            },
            "assets": exported_assets,
            "messages": [{
                "message_id": m.message_id, "role": m.role.value, "content": m.content,
                "actor_id": m.actor_id, "task_id": m.task_id, "run_id": m.run_id,
                "asset_ids": list(m.asset_ids), "metadata": dict(m.metadata),
                "token_usage": (
                    {
                        "prompt_tokens": m.token_usage.prompt_tokens,
                        "completion_tokens": m.token_usage.completion_tokens,
                        "total_tokens": m.token_usage.total_tokens,
                        "model": m.token_usage.model,
                    } if m.token_usage is not None else None
                ),
                "created_at": m.created_at.isoformat(),
            } for m in messages],
        }
        safe_id = quote(session_id, safe="")
        if format == "json":
            content = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            media_type = "application/json"
            filename = f"session-{safe_id}.json"
        else:
            lines = [f"# {session.title or 'Session'}", "", f"- Session: `{session_id}`",
                     f"- Status: {session.status}", ""]
            for message in messages:
                lines.extend([
                    f"## {message.role.value} · {message.created_at.isoformat()}",
                    "", message.content, "",
                ])
            if exported_assets:
                lines.extend(["## Attachments", ""])
                for asset in exported_assets:
                    if asset.get("status") == "unavailable":
                        lines.append(f"- {asset['asset_id']}: unavailable")
                    else:
                        lines.append(
                            f"- {asset['filename']} ({asset['asset_id']}), "
                            f"current version {asset['current_version']}, "
                            f"SHA-256 {asset['sha256']}"
                        )
                        for version in asset["versions"]:
                            lines.append(
                                f"  - v{version['version']}: {version['filename']} "
                                f"({version['size_bytes']} bytes, {version['sha256']})"
                            )
                lines.append("")
            content = "\n".join(lines)
            media_type = "text/markdown; charset=utf-8"
            filename = f"session-{safe_id}.md"
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="session.exported", subject_id=session_id,
                detail={"format": format, "message_count": len(messages)},
            )
        return Response(
            content=content, media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Cache-Control": "no-store",
            },
        )

    @router.post("/sessions/{session_id}/archive")
    async def archive_session(session_id: str, request: Request):
        principal, service = await context(request, "session", session_id, "write")
        changed = await service.repository.set_session_status(
            principal.tenant_id, session_id, "archived", expected_status="active",
        )
        if not changed:
            current = await service.repository.get_session(principal.tenant_id, session_id)
            if current is None or current.status == "deleted":
                raise HTTPException(404, "session not found")
            raise HTTPException(409, "only active sessions can be archived")
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="session.archived", subject_id=session_id, detail={},
            )
        return {"session_id": session_id, "status": "archived"}

    @router.post("/sessions/{session_id}/restore")
    async def restore_session(session_id: str, request: Request):
        principal, service = await context(request, "session", session_id, "write")
        changed = await service.repository.set_session_status(
            principal.tenant_id, session_id, "active", expected_status="archived",
        )
        if not changed:
            current = await service.repository.get_session(principal.tenant_id, session_id)
            if current is None or current.status == "deleted":
                raise HTTPException(404, "session not found")
            raise HTTPException(409, "only archived sessions can be restored")
        if audit_writer is not None:
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="session.restored", subject_id=session_id, detail={},
            )
        return {"session_id": session_id, "status": "active"}

    @router.delete("/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str, request: Request):
        principal, service = await context(request, "session", session_id, "delete")
        session = await service.repository.get_session(principal.tenant_id, session_id)
        if session is None:
            raise HTTPException(404, "session not found")
        if session.status != "deleted":
            changed = await service.repository.set_session_status(
                principal.tenant_id, session_id, "deleted",
                expected_status=session.status,
            )
            if not changed:
                raise HTTPException(409, "session lifecycle changed; retry deletion")
        # Tombstone every file attached to the session and remove its indexed and
        # binary content. The deleted session is hidden before cleanup begins.
        try:
            linked_assets = await service.repository.list_assets(
                principal.tenant_id, session.owner_id, session_id, limit=500,
            )
            for asset in linked_assets:
                await service.delete_asset(
                    tenant_id=principal.tenant_id, asset_id=asset.asset_id,
                )
        except SessionNotFound:
            pass
        except SessionError as exc:
            raise HTTPException(503, str(exc)) from exc
        try:
            await service.repository.delete_session_history(
                principal.tenant_id, session_id,
            )
        except SessionNotFound:
            raise HTTPException(404, "session not found")
        if audit_writer is not None and session.status != "deleted":
            await audit_writer.record(
                tenant_id=principal.tenant_id, actor_id=principal.user_id,
                action="session.deleted", subject_id=session_id,
                detail={"project_id": session.project_id, "status": session.status},
            )
        return Response(status_code=204)

    return router
