"""Tenant-scoped API for knowledge asset registration and lineage events."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Query, Request

from agent_platform.knowledge.asset_lineage import (
    AssetKind, KnowledgeAsset, KnowledgeAssetRepository,
    KnowledgeLineageEdge, LineageRelation,
)
from agent_platform.knowledge.contracts import KnowledgePrincipal

PrincipalResolver = Callable[[Request], Awaitable[KnowledgePrincipal]]


def create_knowledge_asset_router(*, repository: KnowledgeAssetRepository,
                                  resolve_principal: PrincipalResolver) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/knowledge/assets", tags=["knowledge-assets"])

    async def principal_for(request: Request) -> KnowledgePrincipal:
        principal = await resolve_principal(request)
        if not isinstance(principal, KnowledgePrincipal):
            raise HTTPException(401, "authenticated knowledge principal required")
        return principal

    def can_manage(principal: KnowledgePrincipal, project_id: str | None) -> bool:
        return "knowledge.manage" in principal.permissions or (
            project_id is not None and project_id in principal.manage_project_ids
        )

    def serialize(asset: KnowledgeAsset) -> dict[str, Any]:
        return {
            "asset_id": asset.asset_id, "tenant_id": asset.tenant_id, "name": asset.name,
            "kind": asset.kind.value, "owner_id": asset.owner_id,
            "owner_department": asset.owner_department, "business_domain": asset.business_domain,
            "classification": asset.classification, "source_system": asset.source_system,
            "external_id": asset.external_id, "version": asset.version,
            "project_id": asset.project_id, "metadata": dict(asset.metadata),
            "created_at": asset.created_at.isoformat(),
        }

    @router.post("", status_code=201)
    async def register(request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object required")
        project_id = payload.get("project_id")
        if project_id is not None and (not isinstance(project_id, str) or project_id not in principal.project_ids):
            raise HTTPException(404, "project not found")
        if not can_manage(principal, project_id):
            raise HTTPException(403, "knowledge asset management permission denied")
        try:
            asset = KnowledgeAsset(
                tenant_id=principal.tenant_id, name=payload.get("name"),
                kind=AssetKind(payload.get("kind")), owner_id=payload.get("owner_id", principal.user_id),
                owner_department=payload.get("owner_department"), business_domain=payload.get("business_domain"),
                classification=payload.get("classification"), source_system=payload.get("source_system"),
                external_id=payload.get("external_id"), project_id=project_id,
                metadata=payload.get("metadata", {}),
            )
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if not await repository.register_asset(asset):
            raise HTTPException(409, "asset already registered")
        return serialize(asset)

    @router.get("")
    async def list_assets(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> dict[str, Any]:
        principal = await principal_for(request)
        assets = await repository.list_assets(principal.tenant_id, principal.project_ids, limit)
        return {"items": [serialize(asset) for asset in assets]}

    @router.get("/{asset_id}")
    async def get_asset(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        asset = await repository.get_asset(principal.tenant_id, asset_id)
        if asset is None or (asset.project_id is not None and asset.project_id not in principal.project_ids):
            raise HTTPException(404, "asset not found")
        return serialize(asset)

    @router.post("/{asset_id}/lineage", status_code=201)
    async def add_lineage(asset_id: str, request: Request) -> dict[str, Any]:
        principal = await principal_for(request)
        payload = await request.json()
        if not isinstance(payload, dict):
            raise HTTPException(400, "JSON object required")
        source_id = payload.get("source_id", asset_id)
        target_id = payload.get("target_id")
        if source_id != asset_id and target_id != asset_id:
            raise HTTPException(400, "path asset_id must be one lineage endpoint")
        try:
            relation = LineageRelation(payload.get("relation"))
            source = await repository.get_asset(principal.tenant_id, source_id)
            target = await repository.get_asset(principal.tenant_id, target_id)
            if source is None or target is None:
                raise HTTPException(404, "lineage endpoint not found")
            if any(a.project_id is not None and a.project_id not in principal.project_ids for a in (source, target)):
                raise HTTPException(404, "lineage endpoint not found")
            project_id = source.project_id or target.project_id
            if not can_manage(principal, project_id):
                raise HTTPException(403, "knowledge asset management permission denied")
            edge = KnowledgeLineageEdge(
                tenant_id=principal.tenant_id, source_id=source_id, target_id=target_id,
                relation=relation, actor_id=principal.user_id, project_id=project_id,
                metadata=payload.get("metadata", {}),
            )
            created = await repository.add_lineage(edge)
        except HTTPException:
            raise
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        if not created:
            raise HTTPException(409, "lineage relation already exists")
        return {"edge_id": edge.edge_id, "source_id": edge.source_id,
                "target_id": edge.target_id, "relation": edge.relation.value,
                "project_id": edge.project_id, "created_at": edge.occurred_at.isoformat()}

    @router.get("/{asset_id}/lineage")
    async def get_lineage(asset_id: str, request: Request,
                          direction: str = Query(default="both"),
                          depth: int = Query(default=1, ge=1, le=20)) -> dict[str, Any]:
        principal = await principal_for(request)
        asset = await repository.get_asset(principal.tenant_id, asset_id)
        if asset is None or (asset.project_id is not None and asset.project_id not in principal.project_ids):
            raise HTTPException(404, "asset not found")
        if not can_manage(principal, asset.project_id):
            raise HTTPException(403, "knowledge asset management permission denied")
        try:
            edges = await repository.get_lineage(principal.tenant_id, asset_id, direction=direction, depth=depth)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        nodes = {}
        for edge in edges:
            for node_id in (edge.source_id, edge.target_id):
                node = await repository.get_asset(principal.tenant_id, node_id)
                if node is not None:
                    nodes[node_id] = serialize(node)
        return {"asset_id": asset_id, "direction": direction, "depth": depth,
                "nodes": list(nodes.values()), "edges": [
                    {"edge_id": e.edge_id, "source_id": e.source_id, "target_id": e.target_id,
                     "relation": e.relation.value, "actor_id": e.actor_id,
                     "created_at": e.occurred_at.isoformat(), "metadata": dict(e.metadata)}
                    for e in edges
                ]}

    return router
