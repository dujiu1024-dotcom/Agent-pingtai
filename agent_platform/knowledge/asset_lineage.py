"""Knowledge asset registration and processing-lineage contracts.

This module owns governance metadata only. Parsing, embedding, indexing and RAG
execution remain owned by the existing knowledge pipeline; callers project
their stage events into this lineage graph.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, Protocol
from uuid import uuid4


class AssetKind(str, Enum):
    ASSET = "asset"
    DATASET = "dataset"
    DOCUMENT = "document"
    SOURCE_SYSTEM = "source_system"
    PROCESSING_STAGE = "processing_stage"
    EMBEDDING = "embedding"
    INDEX = "index"
    RETRIEVAL = "retrieval"
    CITATION = "citation"
    AGENT = "agent"
    PROMPT = "prompt"
    WORKFLOW = "workflow"


class LineageRelation(str, Enum):
    CONTAINS = "contains"
    SOURCED_FROM = "sourced_from"
    COLLECTED_FROM = "collected_from"
    PARSED_FROM = "parsed_from"
    CHUNKED_FROM = "chunked_from"
    EMBEDDED_FROM = "embedded_from"
    INDEXED_FROM = "indexed_from"
    RETRIEVED_FROM = "retrieved_from"
    CITED_FROM = "cited_from"
    USED_BY = "used_by"
    DERIVED_FROM = "derived_from"


@dataclass(frozen=True)
class KnowledgeAsset:
    tenant_id: str
    name: str
    kind: AssetKind
    owner_id: str
    owner_department: str
    business_domain: str
    classification: str
    source_system: str | None = None
    external_id: str | None = None
    version: int = 1
    project_id: str | None = None
    asset_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: Mapping[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for name in ("tenant_id", "name", "owner_id", "owner_department", "business_domain", "classification", "asset_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if not isinstance(self.kind, AssetKind):
            raise ValueError("kind must be an AssetKind")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if self.project_id is not None and (not isinstance(self.project_id, str) or not self.project_id.strip()):
            raise ValueError("project_id must be a non-empty string")
        if self.source_system is not None and (not isinstance(self.source_system, str) or not self.source_system.strip()):
            raise ValueError("source_system must be a non-empty string")
        if self.external_id is not None and (not isinstance(self.external_id, str) or not self.external_id.strip()):
            raise ValueError("external_id must be a non-empty string")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be an object")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")


@dataclass(frozen=True)
class KnowledgeLineageEdge:
    tenant_id: str
    source_id: str
    target_id: str
    relation: LineageRelation
    actor_id: str
    edge_id: str = field(default_factory=lambda: str(uuid4()))
    project_id: str | None = None
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in ("tenant_id", "source_id", "target_id", "actor_id", "edge_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        if self.source_id == self.target_id:
            raise ValueError("lineage edge cannot point to itself")
        if not isinstance(self.relation, LineageRelation):
            raise ValueError("relation must be a LineageRelation")
        if self.project_id is not None and (not isinstance(self.project_id, str) or not self.project_id.strip()):
            raise ValueError("project_id must be a non-empty string")
        if not isinstance(self.occurred_at, datetime) or self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")
        if not isinstance(self.metadata, Mapping):
            raise ValueError("metadata must be an object")


class KnowledgeAssetRepository(Protocol):
    async def register_asset(self, asset: KnowledgeAsset) -> bool: ...
    async def get_asset(self, tenant_id: str, asset_id: str) -> KnowledgeAsset | None: ...
    async def list_assets(self, tenant_id: str, project_ids: frozenset[str], limit: int = 100) -> tuple[KnowledgeAsset, ...]: ...
    async def add_lineage(self, edge: KnowledgeLineageEdge) -> bool: ...
    async def get_lineage(self, tenant_id: str, asset_id: str, *, direction: str = "both", depth: int = 1) -> tuple[KnowledgeLineageEdge, ...]: ...


class InMemoryKnowledgeAssetRepository:
    """Test adapter with tenant-bound endpoints and cycle-safe lineage."""

    def __init__(self) -> None:
        self._assets: dict[tuple[str, str], KnowledgeAsset] = {}
        self._edges: dict[tuple[str, str], KnowledgeLineageEdge] = {}
        self._lock = asyncio.Lock()

    async def register_asset(self, asset: KnowledgeAsset) -> bool:
        async with self._lock:
            key = (asset.tenant_id, asset.asset_id)
            if key in self._assets:
                return False
            if asset.external_id and any(
                item.tenant_id == asset.tenant_id and item.kind is asset.kind
                and item.source_system == asset.source_system and item.external_id == asset.external_id
                for item in self._assets.values()
            ):
                return False
            self._assets[key] = asset
            return True

    async def get_asset(self, tenant_id: str, asset_id: str) -> KnowledgeAsset | None:
        async with self._lock:
            return self._assets.get((tenant_id, asset_id))

    async def list_assets(self, tenant_id: str, project_ids: frozenset[str], limit: int = 100) -> tuple[KnowledgeAsset, ...]:
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        async with self._lock:
            rows = [a for (tenant, _), a in self._assets.items() if tenant == tenant_id and (a.project_id is None or a.project_id in project_ids)]
            return tuple(sorted(rows, key=lambda a: a.created_at, reverse=True)[:limit])

    async def add_lineage(self, edge: KnowledgeLineageEdge) -> bool:
        async with self._lock:
            key = (edge.tenant_id, edge.edge_id)
            if key in self._edges:
                return False
            source = self._assets.get((edge.tenant_id, edge.source_id))
            target = self._assets.get((edge.tenant_id, edge.target_id))
            if source is None or target is None:
                raise ValueError("lineage endpoints must exist in the same tenant")
            if source.project_id not in (None, edge.project_id) or target.project_id not in (None, edge.project_id):
                raise ValueError("lineage endpoints cross project boundaries")
            if any(e.tenant_id == edge.tenant_id and e.source_id == edge.source_id and e.target_id == edge.target_id and e.relation is edge.relation for e in self._edges.values()):
                return False
            # Edges are directed source -> target. Reject cycles to keep lineage traversals finite.
            stack = [edge.target_id]
            visited: set[str] = set()
            while stack:
                current = stack.pop()
                if current == edge.source_id:
                    raise ValueError("lineage edge would create a cycle")
                if current in visited:
                    continue
                visited.add(current)
                stack.extend(e.target_id for e in self._edges.values() if e.tenant_id == edge.tenant_id and e.source_id == current)
            self._edges[key] = edge
            return True

    async def get_lineage(self, tenant_id: str, asset_id: str, *, direction: str = "both", depth: int = 1) -> tuple[KnowledgeLineageEdge, ...]:
        if direction not in {"upstream", "downstream", "both"}:
            raise ValueError("direction must be upstream, downstream or both")
        if type(depth) is not int or not 1 <= depth <= 20:
            raise ValueError("depth must be between 1 and 20")
        async with self._lock:
            if (tenant_id, asset_id) not in self._assets:
                return ()
            frontier = {asset_id}
            seen: set[str] = set()
            result: dict[str, KnowledgeLineageEdge] = {}
            for _ in range(depth):
                next_frontier: set[str] = set()
                for edge in self._edges.values():
                    if edge.tenant_id != tenant_id:
                        continue
                    matches = []
                    if direction in {"downstream", "both"} and edge.source_id in frontier:
                        matches.append(edge.target_id)
                    if direction in {"upstream", "both"} and edge.target_id in frontier:
                        matches.append(edge.source_id)
                    if matches:
                        result[edge.edge_id] = edge
                        next_frontier.update(matches)
                next_frontier -= seen
                seen.update(frontier)
                frontier = next_frontier
                if not frontier:
                    break
            return tuple(sorted(result.values(), key=lambda e: (e.occurred_at, e.edge_id)))
