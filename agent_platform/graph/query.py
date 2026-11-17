"""Bounded graph query DSL, ACL-first orchestration and hybrid retrieval."""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from enum import Enum
from time import monotonic
from typing import Any, Mapping, Protocol, Sequence

from agent_platform.knowledge.contracts import KnowledgePrincipal

_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,199}$")
_MAX_QUERY_CHARS = 4096


class GraphQueryMode(str, Enum):
    NEIGHBORS = "neighbors"
    PATHS = "paths"
    SUBGRAPH = "subgraph"


class GraphQueryError(ValueError):
    """Invalid graph query request."""


class GraphQueryAuthorizationError(PermissionError):
    """A graph adapter returned data outside the authorized tenant/project scope."""


class GraphQueryTimeout(TimeoutError):
    """A bounded graph or hybrid query exceeded its time budget."""


@dataclass(frozen=True)
class GraphQuerySpec:
    mode: GraphQueryMode
    start_entity_ids: tuple[str, ...]
    relation_types: tuple[str, ...] = ()
    entity_types: tuple[str, ...] = ()
    max_depth: int = 2
    limit: int = 50
    timeout_ms: int = 750

    def __post_init__(self) -> None:
        if not isinstance(self.mode, GraphQueryMode):
            raise GraphQueryError("mode must be a GraphQueryMode")
        if not isinstance(self.start_entity_ids, tuple) or not self.start_entity_ids:
            raise GraphQueryError("start_entity_ids must be a non-empty tuple")
        if len(self.start_entity_ids) > 20:
            raise GraphQueryError("at most 20 start entities are allowed")
        for value in self.start_entity_ids:
            if not isinstance(value, str) or not _RECORD_ID.fullmatch(value):
                raise GraphQueryError("start entity IDs must be canonical identifiers")
        if len(set(self.start_entity_ids)) != len(self.start_entity_ids):
            raise GraphQueryError("start_entity_ids must be unique")
        for label, values in (
            ("relation_types", self.relation_types),
            ("entity_types", self.entity_types),
        ):
            if not isinstance(values, tuple) or len(values) > 20:
                raise GraphQueryError(f"{label} must be a tuple with at most 20 values")
            if any(not isinstance(value, str) or not _IDENTIFIER.fullmatch(value) for value in values):
                raise GraphQueryError(f"{label} must contain canonical identifiers")
            if len(set(values)) != len(values):
                raise GraphQueryError(f"{label} must be unique")
        if type(self.max_depth) is not int or not 1 <= self.max_depth <= 5:
            raise GraphQueryError("max_depth must be an integer between 1 and 5")
        if type(self.limit) is not int or not 1 <= self.limit <= 100:
            raise GraphQueryError("limit must be an integer between 1 and 100")
        if type(self.timeout_ms) is not int or not 100 <= self.timeout_ms <= 5000:
            raise GraphQueryError("timeout_ms must be an integer between 100 and 5000")

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "GraphQuerySpec":
        if not isinstance(payload, Mapping):
            raise GraphQueryError("graph query must be an object")
        allowed = {
            "mode", "start_entity_ids", "relation_types", "entity_types",
            "max_depth", "limit", "timeout_ms",
        }
        unknown = set(payload) - allowed
        if unknown:
            raise GraphQueryError(f"unknown graph query fields: {sorted(map(str, unknown))}")
        try:
            mode = GraphQueryMode(payload.get("mode", "neighbors"))
        except (TypeError, ValueError) as exc:
            raise GraphQueryError("unsupported graph query mode") from exc
        starts = payload.get("start_entity_ids")
        if not isinstance(starts, list):
            raise GraphQueryError("start_entity_ids must be a list")
        relations = payload.get("relation_types", [])
        entities = payload.get("entity_types", [])
        if not isinstance(relations, list) or not isinstance(entities, list):
            raise GraphQueryError("relation_types and entity_types must be lists")
        return cls(
            mode=mode,
            start_entity_ids=tuple(starts),
            relation_types=tuple(relations),
            entity_types=tuple(entities),
            max_depth=payload.get("max_depth", 2),
            limit=payload.get("limit", 50),
            timeout_ms=payload.get("timeout_ms", 750),
        )


@dataclass(frozen=True)
class GraphQueryResult:
    items: tuple[Mapping[str, Any], ...]
    truncated: bool
    elapsed_ms: int


@dataclass(frozen=True)
class GraphHybridResult:
    items: tuple[Mapping[str, Any], ...]
    truncated: bool
    elapsed_ms: int


class GraphQueryRepository(Protocol):
    async def execute_query(
        self,
        *,
        tenant_id: str,
        project_ids: frozenset[str],
        spec: GraphQuerySpec,
    ) -> Sequence[Mapping[str, Any]]: ...


class GraphVectorRetriever(Protocol):
    async def retrieve(
        self,
        *,
        principal: KnowledgePrincipal,
        query: str,
        candidate_entity_ids: tuple[str, ...],
        limit: int,
    ) -> Sequence[Mapping[str, Any]]: ...


def _validate_scoped_record(
    row: Mapping[str, Any], *, tenant_id: str, project_ids: frozenset[str],
) -> None:
    if row.get("tenant_id") != tenant_id:
        raise GraphQueryAuthorizationError("graph repository returned a cross-tenant record")
    project_id = row.get("project_id")
    if project_id is not None and project_id not in project_ids:
        raise GraphQueryAuthorizationError("graph repository returned an unauthorized project record")


class GraphQueryService:
    """Pass principal-derived scope to adapters and fail closed on bad results."""

    def __init__(
        self,
        repository: GraphQueryRepository,
        *,
        vector_retriever: GraphVectorRetriever | None = None,
        max_timeout_ms: int = 5000,
    ) -> None:
        if type(max_timeout_ms) is not int or not 100 <= max_timeout_ms <= 5000:
            raise ValueError("max_timeout_ms must be between 100 and 5000")
        self.repository = repository
        self.vector_retriever = vector_retriever
        self.max_timeout_ms = max_timeout_ms

    @staticmethod
    def _scope(principal: KnowledgePrincipal) -> tuple[str, frozenset[str]]:
        if not isinstance(principal, KnowledgePrincipal):
            raise GraphQueryAuthorizationError("an authenticated KnowledgePrincipal is required")
        if not isinstance(principal.tenant_id, str) or not principal.tenant_id.strip():
            raise GraphQueryAuthorizationError("principal tenant is required")
        projects = principal.project_ids | principal.manage_project_ids
        if any(not isinstance(item, str) or not item.strip() for item in projects):
            raise GraphQueryAuthorizationError("principal project scope is invalid")
        return principal.tenant_id, frozenset(projects)

    @staticmethod
    def _authorized_rows(
        rows: Sequence[Mapping[str, Any]],
        *,
        tenant_id: str,
        project_ids: frozenset[str],
    ) -> tuple[Mapping[str, Any], ...]:
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
            raise GraphQueryError("graph repository must return a sequence of records")
        result: list[Mapping[str, Any]] = []
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, Mapping):
                raise GraphQueryError("graph repository returned a non-object record")
            _validate_scoped_record(
                row, tenant_id=tenant_id, project_ids=project_ids,
            )
            entity_id = row.get("entity_id")
            if not isinstance(entity_id, str) or not _RECORD_ID.fullmatch(entity_id):
                raise GraphQueryError("graph repository returned an invalid entity_id")
            path = row.get("path", ())
            if path:
                if not isinstance(path, Sequence) or isinstance(path, (str, bytes)):
                    raise GraphQueryError("path result must contain a sequence of nodes")
                for node in path:
                    if not isinstance(node, Mapping):
                        raise GraphQueryError("path result contains a non-object node")
                    _validate_scoped_record(
                        node, tenant_id=tenant_id, project_ids=project_ids,
                    )
                    node_id = node.get("entity_id")
                    if not isinstance(node_id, str) or not _RECORD_ID.fullmatch(node_id):
                        raise GraphQueryError("path result contains an invalid entity_id")
            result_id = row.get("result_id", entity_id)
            if not isinstance(result_id, str) or not result_id:
                raise GraphQueryError("graph repository returned an invalid result_id")
            if result_id in seen:
                continue
            seen.add(result_id)
            result.append(dict(row))
        return tuple(result)

    async def query(
        self, *, principal: KnowledgePrincipal, spec: GraphQuerySpec,
    ) -> GraphQueryResult:
        tenant_id, project_ids = self._scope(principal)
        if not isinstance(spec, GraphQuerySpec):
            raise GraphQueryError("spec must be a GraphQuerySpec")
        timeout_ms = min(spec.timeout_ms, self.max_timeout_ms)
        started = monotonic()
        try:
            rows = await asyncio.wait_for(
                self.repository.execute_query(
                    tenant_id=tenant_id, project_ids=project_ids, spec=spec,
                ),
                timeout=timeout_ms / 1000,
            )
        except asyncio.TimeoutError as exc:
            raise GraphQueryTimeout("graph query exceeded its time budget") from exc
        authorized = self._authorized_rows(
            rows, tenant_id=tenant_id, project_ids=project_ids,
        )
        truncated = len(authorized) > spec.limit
        return GraphQueryResult(
            items=authorized[:spec.limit],
            truncated=truncated,
            elapsed_ms=max(0, int((monotonic() - started) * 1000)),
        )

    async def hybrid_search(
        self,
        *,
        principal: KnowledgePrincipal,
        query: str,
        spec: GraphQuerySpec,
    ) -> GraphHybridResult:
        if self.vector_retriever is None:
            raise GraphQueryError("hybrid retrieval is not configured")
        if not isinstance(query, str) or not query.strip() or len(query) > _MAX_QUERY_CHARS:
            raise GraphQueryError("query must contain 1-4096 characters")
        started = monotonic()
        graph_result = await self.query(principal=principal, spec=spec)
        candidates = tuple(item["entity_id"] for item in graph_result.items)
        if not candidates:
            return GraphHybridResult((), graph_result.truncated, graph_result.elapsed_ms)
        total_budget = min(spec.timeout_ms, self.max_timeout_ms) / 1000
        remaining = total_budget - (monotonic() - started)
        if remaining <= 0:
            raise GraphQueryTimeout("hybrid retrieval exceeded its time budget")
        try:
            vector_rows = await asyncio.wait_for(
                self.vector_retriever.retrieve(
                    principal=principal, query=query,
                    candidate_entity_ids=candidates, limit=spec.limit,
                ),
                timeout=min(remaining, self.max_timeout_ms / 1000),
            )
        except asyncio.TimeoutError as exc:
            raise GraphQueryTimeout("hybrid vector retrieval exceeded its time budget") from exc
        tenant_id, project_ids = self._scope(principal)
        authorized_vectors = self._authorized_rows(
            vector_rows, tenant_id=tenant_id, project_ids=project_ids,
        )
        candidate_set = set(candidates)
        if any(row["entity_id"] not in candidate_set for row in authorized_vectors):
            raise GraphQueryAuthorizationError("vector retriever returned an entity outside graph candidates")

        # Reciprocal-rank fusion combines graph and vector ranks without trusting
        # incompatible raw score scales from separate providers.
        fused: dict[str, dict[str, Any]] = {}
        for rank, item in enumerate(graph_result.items, start=1):
            fused[item["entity_id"]] = {
                "entity_id": item["entity_id"], "tenant_id": tenant_id,
                "project_id": item.get("project_id"), "graph": item,
                "vector": None, "score": 1.0 / (60 + rank),
            }
        for rank, item in enumerate(authorized_vectors, start=1):
            entity_id = item["entity_id"]
            target = fused.setdefault(entity_id, {
                "entity_id": entity_id, "tenant_id": tenant_id,
                "project_id": item.get("project_id"), "graph": None,
                "vector": None, "score": 0.0,
            })
            target["vector"] = item
            target["score"] += 1.0 / (60 + rank)
        ranked = sorted(fused.values(), key=lambda item: (-item["score"], item["entity_id"]))
        return GraphHybridResult(
            items=tuple(ranked[:spec.limit]),
            truncated=graph_result.truncated or len(ranked) > spec.limit,
            elapsed_ms=max(0, int((monotonic() - started) * 1000)),
        )
