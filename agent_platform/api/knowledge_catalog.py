"""HTTP API for tenant-scoped knowledge collection and index catalogs.

The host supplies an authenticated KnowledgePrincipal resolver. Tenant and user
identifiers are never accepted from request payloads.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query, Request

from agent_platform.knowledge.catalog import (
    KnowledgeCatalogRepository,
    KnowledgeCollection,
    KnowledgeIndex,
)
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.service import KnowledgeService

PrincipalResolver = Callable[[Request], Awaitable[KnowledgePrincipal]]


def create_knowledge_catalog_router(
    *,
    catalog: KnowledgeCatalogRepository,
    knowledge: KnowledgeService,
    resolve_principal: PrincipalResolver,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/knowledge", tags=["knowledge-catalog"])

    async def principal_for(request: Request) -> KnowledgePrincipal:
        principal = await resolve_principal(request)
        if not isinstance(principal, KnowledgePrincipal):
            raise HTTPException(401, "authenticated knowledge principal required")
        return principal

    def can_manage(principal: KnowledgePrincipal, project_id: str | None) -> bool:
        return (
            "knowledge.manage" in principal.permissions
            or (project_id is not None and project_id in principal.manage_project_ids)
        )

    @router.post("/collections", status_code=201)
    async def create_collection(request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object required")
        name = payload.get("name")
        description = payload.get("description", "")
        project_id = payload.get("project_id")
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(400, "name is required")
        if not isinstance(description, str):
            raise HTTPException(400, "description must be a string")
        if project_id is not None and (
            not isinstance(project_id, str) or project_id not in principal.project_ids
        ):
            raise HTTPException(404, "project not found")
        if not can_manage(principal, project_id):
            raise HTTPException(403, "collection management permission denied")
        collection = KnowledgeCollection(
            tenant_id=principal.tenant_id,
            name=name.strip(),
            description=description,
            project_id=project_id,
            created_by=principal.user_id,
        )
        created = await catalog.create_collection(collection)
        if not created:
            raise HTTPException(409, "collection already exists")
        return {
            "collection_id": collection.collection_id,
            "tenant_id": collection.tenant_id,
            "name": collection.name,
            "description": collection.description,
            "project_id": collection.project_id,
            "created_at": collection.created_at.isoformat(),
        }

    @router.get("/collections")
    async def list_collections(
        request: Request, limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, Any]:
        principal = await principal_for(request)
        items = await catalog.list_collections(
            principal.tenant_id, principal.project_ids, limit=limit,
        )
        return {"items": [{
            "collection_id": item.collection_id,
            "name": item.name,
            "description": item.description,
            "project_id": item.project_id,
            "created_at": item.created_at.isoformat(),
        } for item in items]}

    @router.get("/collections/{collection_id}")
    async def get_collection(
        collection_id: str, request: Request,
    ) -> dict[str, Any]:
        principal = await principal_for(request)
        item = await catalog.get_collection(principal.tenant_id, collection_id)
        if item is None or (
            item.project_id is not None and item.project_id not in principal.project_ids
        ):
            raise HTTPException(404, "collection not found")
        return {
            "collection_id": item.collection_id,
            "name": item.name,
            "description": item.description,
            "project_id": item.project_id,
            "created_at": item.created_at.isoformat(),
        }

    @router.post("/collections/{collection_id}/documents", status_code=201)
    async def attach_document(
        collection_id: str, request: Request,
    ) -> dict[str, Any]:
        principal = await principal_for(request)
        collection = await catalog.get_collection(principal.tenant_id, collection_id)
        if collection is None or (
            collection.project_id is not None
            and collection.project_id not in principal.project_ids
        ):
            raise HTTPException(404, "collection not found")
        if not can_manage(principal, collection.project_id):
            raise HTTPException(403, "collection management permission denied")
        payload = await request.json()
        document_id = payload.get("document_id") if isinstance(payload, dict) else None
        if not isinstance(document_id, str) or not document_id.strip():
            raise HTTPException(400, "document_id is required")
        document = await knowledge.repository.get_document(
            principal.tenant_id, document_id,
        )
        if document is None:
            raise HTTPException(404, "document not found")
        if document.scope.value == "private":
            raise HTTPException(404, "document not found")
        if collection.project_id is None:
            if document.scope.value != "tenant":
                raise HTTPException(404, "document not found")
        elif (
            document.scope.value != "project"
            or document.project_id != collection.project_id
        ):
            raise HTTPException(404, "document not found")
        try:
            created = await catalog.attach_document(
                principal.tenant_id, collection_id, document_id,
            )
        except ValueError as exc:
            raise HTTPException(404, "collection not found") from exc
        return {"attached": created, "document_id": document_id}

    @router.post("/collections/{collection_id}/indexes", status_code=201)
    async def create_index(
        collection_id: str, request: Request,
    ) -> dict[str, Any]:
        principal = await principal_for(request)
        collection = await catalog.get_collection(principal.tenant_id, collection_id)
        if collection is None or (
            collection.project_id is not None
            and collection.project_id not in principal.project_ids
        ):
            raise HTTPException(404, "collection not found")
        if not can_manage(principal, collection.project_id):
            raise HTTPException(403, "index management permission denied")
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object required")
        try:
            index = KnowledgeIndex(
                tenant_id=principal.tenant_id,
                collection_id=collection_id,
                index_name=payload.get("index_name", ""),
                embedding_model=payload.get("embedding_model", ""),
                dimensions=payload.get("dimensions", 0),
                schema_version=payload.get("schema_version", 1),
            )
            created = await catalog.create_index(index)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if not created:
            raise HTTPException(409, "index already exists")
        return {
            "index_id": index.index_id,
            "collection_id": index.collection_id,
            "index_name": index.index_name,
            "embedding_model": index.embedding_model,
            "dimensions": index.dimensions,
            "schema_version": index.schema_version,
            "status": index.status.value,
        }

    return router
