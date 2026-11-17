"""Authenticated upload endpoint for the knowledge ingestion pipeline.

Uploads are streamed with a strict byte limit, parsed by the shared knowledge
ParserRegistry, chunked with source locators, embedded, and persisted through
KnowledgeService. Tenant identity is always taken from the authenticated principal.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.knowledge.contracts import (
    Document,
    KnowledgePrincipal,
    KnowledgeScope,
)
from agent_platform.knowledge.service import KnowledgeService

PrincipalResolver = Callable[[Request], Awaitable[KnowledgePrincipal]]
_ALLOWED_MEDIA_TYPES = {
    "application/pdf",
    "text/markdown",
    "text/x-markdown",
    "text/html",
    "application/xhtml+xml",
}
_EXTENSION_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
}


def create_knowledge_upload_router(
    *, knowledge: KnowledgeService, resolve_principal: PrincipalResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/knowledge", tags=["knowledge-ingestion"])

    @router.post("/documents/upload", status_code=201)
    async def upload_document(request: Request) -> dict:
        principal = await resolve_principal(request)
        if not isinstance(principal, KnowledgePrincipal):
            raise HTTPException(401, "authenticated knowledge principal required")
        if knowledge.embedder is None:
            raise HTTPException(503, "knowledge embedding provider is not configured")

        query = request.query_params
        filename = query.get("filename") or request.headers.get("x-filename", "")
        if not filename or len(filename) > 255 or filename in {".", ".."}:
            raise HTTPException(400, "a valid filename is required")
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if media_type in {"application/octet-stream", ""}:
            media_type = _EXTENSION_MEDIA_TYPES.get(suffix, "")
        if media_type not in _ALLOWED_MEDIA_TYPES:
            raise HTTPException(415, "only PDF, Markdown and HTML documents are supported")
        if suffix and suffix not in _EXTENSION_MEDIA_TYPES:
            raise HTTPException(415, "filename extension is not supported")
        expected = _EXTENSION_MEDIA_TYPES.get(suffix)
        if expected is not None and media_type != expected:
            raise HTTPException(415, "filename extension and content type do not match")

        title = query.get("title") or filename.rsplit(".", 1)[0]
        if not title.strip() or len(title) > 200:
            raise HTTPException(400, "title must contain 1-200 characters")
        scope_value = query.get("scope", "tenant")
        try:
            scope = KnowledgeScope(scope_value)
        except ValueError as exc:
            raise HTTPException(400, "scope must be tenant, project or private") from exc
        project_id = query.get("project_id")
        if scope is KnowledgeScope.PROJECT:
            if not project_id or project_id not in principal.project_ids:
                raise HTTPException(404, "project not found")
            if "knowledge.manage" not in principal.permissions and project_id not in principal.manage_project_ids:
                raise HTTPException(403, "project knowledge.manage permission required")
        elif project_id is not None:
            raise HTTPException(400, "project_id is only valid for project-scoped documents")
        elif scope is KnowledgeScope.TENANT and "knowledge.manage" not in principal.permissions:
            raise HTTPException(403, "knowledge.manage permission required")
        collection_id = query.get("collection_id")
        if scope is KnowledgeScope.PRIVATE and not principal.user_id:
            raise HTTPException(401, "authenticated user required")
        if collection_id is not None:
            collection = await knowledge.repository.get_collection(
                principal.tenant_id, collection_id,
            )
            if collection is None:
                raise HTTPException(404, "collection not found")
            if collection.project_id is not None:
                if collection.project_id not in principal.project_ids:
                    raise HTTPException(404, "collection not found")
                if scope is not KnowledgeScope.PROJECT or project_id != collection.project_id:
                    raise HTTPException(400, "project collection requires a document in the same project")
                if "knowledge.manage" not in principal.permissions and collection.project_id not in principal.manage_project_ids:
                    raise HTTPException(403, "project collection management permission required")
            elif scope is not KnowledgeScope.TENANT:
                raise HTTPException(400, "tenant collection only accepts tenant-scoped documents")

        content = bytearray()
        async for part in request.stream():
            content.extend(part)
            if len(content) > knowledge.max_upload_bytes:
                raise HTTPException(413, "document exceeds configured upload size limit")
        if not content:
            raise HTTPException(400, "uploaded document is empty")

        import uuid

        document_id = str(uuid.uuid4())
        document = Document(
            tenant_id=principal.tenant_id,
            document_id=document_id,
            title=title.strip(),
            source_uri=f"upload://{document_id}/{filename}",
            content_hash="pending",
            scope=scope,
            project_id=project_id,
            owner_user_id=principal.user_id if scope is KnowledgeScope.PRIVATE else None,
            media_type=media_type,
            collection_id=collection_id,
            metadata={
                "ingestion_source": "http_upload",
                "original_filename": filename,
                "uploaded_by": principal.user_id,
            },
        )
        try:
            result = await knowledge.ingest(document=document, content=bytes(content))
        except PermissionError as exc:
            raise HTTPException(403, "document ingestion is not authorized") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, "knowledge ingestion is unavailable") from exc

        return {
            "document_id": result.document.document_id,
            "document_version": result.document.version,
            "collection_id": result.document.collection_id,
            "media_type": result.document.media_type,
            "content_hash": result.document.content_hash,
            "chunk_count": len(result.chunks),
            "embedded": bool(result.chunks),
            "embedding_model": getattr(knowledge.embedder, "storage_key", knowledge.embedder.model_id),
            "skipped_unchanged": result.skipped_unchanged,
            "chunks": [{
                "chunk_id": chunk.chunk_id,
                "ordinal": chunk.ordinal,
                "content_hash": chunk.content_hash,
                "start_offset": chunk.start_offset,
                "end_offset": chunk.end_offset,
                "page_number": chunk.page_number,
                "source_uri": chunk.metadata.get("source_uri"),
            } for chunk in result.chunks],
        }

    return router
