"""Bounded graph traversal, path and ACL-aware hybrid retrieval contracts."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Protocol, Sequence

from agent_platform.graph.schema import GraphSourceDomain


class GraphQueryRepository(Protocol):
    """Tenant-bound graph storage interface; implementations enforce project scope."""

    async def get_entity(
        self, entity_id: str, *, allowed_project_ids: Sequence[str] = ()
    ) -> Any: ...

    async def list_relations(
        self, entity_id: str, *, direction: str = "both",
        allowed_project_ids: Sequence[str] = (), limit: int = 200,
    ) -> list[Any]: ...

    async def search_entities(
        self, query: str, *, entity_type: str | None = None,
        allowed_project_ids: Sequence[str] = (), limit: int = 50,
    ) -> list[Any]: ...


class AclAwareDocumentRetriever(Protocol):
    async def retrieve(
        self, *, query: str, candidate_document_ids: Sequence[str],
        allowed_project_ids: Sequence[str], limit: int,
    ) -> Sequence[Any]:
        """Must re-check canonical document/chunk ACLs; graph scope is not authorization."""
        ...


@dataclass(frozen=True)
class GraphPath:
    entity_ids: tuple[str, ...]
    relation_ids: tuple[str, ...]


@dataclass(frozen=True)
class GraphQueryResult:
    entities: tuple[Any, ...]
    relations: tuple[Any, ...]
    truncated: bool
    visited_count: int


@dataclass(frozen=True)
class GraphHybridResult:
    graph: GraphQueryResult
    document_hits: tuple[Any, ...]
    candidate_document_ids: tuple[str, ...]


class GraphQueryService:
    """Tenant/project-scoped graph query facade with hard traversal budgets."""

    def __init__(
        self, *, tenant_id: str, repository: GraphQueryRepository,
        default_timeout_seconds: float = 2.0,
    ) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if (
            isinstance(default_timeout_seconds, bool)
            or not isinstance(default_timeout_seconds, (int, float))
            or not 0 < default_timeout_seconds <= 30
        ):
            raise ValueError("default_timeout_seconds must be in (0, 30]")
        repository_tenant = getattr(repository, "tenant_id", tenant_id)
        if repository_tenant != tenant_id:
            raise ValueError("graph repository tenant does not match query service tenant")
        self.tenant_id = tenant_id
        self.repository = repository
        self.default_timeout_seconds = float(default_timeout_seconds)

    @staticmethod
    def _scope(allowed_project_ids: Sequence[str]) -> tuple[str, ...]:
        if isinstance(allowed_project_ids, (str, bytes)):
            raise ValueError("allowed_project_ids must be a sequence of project IDs")
        values = tuple(allowed_project_ids)
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("allowed project IDs must be non-empty strings")
        if len(values) != len(set(values)):
            raise ValueError("allowed project IDs must be unique")
        return values

    @staticmethod
    def _positive_int(value: int, name: str, maximum: int) -> None:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be an integer between 1 and {maximum}")

    def _timeout(self, timeout_seconds: float | None) -> float:
        value = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 < value <= 30
        ):
            raise ValueError("timeout_seconds must be in (0, 30]")
        return float(value)

    @staticmethod
    def _entity(record: Any) -> Any:
        return getattr(record, "entity", record)

    @staticmethod
    def _relation(record: Any) -> Any:
        return getattr(record, "relation", record)

    @staticmethod
    def _relation_endpoints(relation: Any) -> tuple[str, str, str]:
        source = getattr(relation, "source_entity_id", None)
        target = getattr(relation, "target_entity_id", None)
        relation_id = getattr(relation, "relation_id", None)
        if not all(isinstance(value, str) and value for value in (source, target, relation_id)):
            raise ValueError("repository returned an invalid graph relation")
        return source, target, relation_id

    async def neighbors(
        self, entity_id: str, *, direction: str = "both",
        relation_types: Sequence[str] = (), depth: int = 1,
        allowed_project_ids: Sequence[str] = (), limit: int = 100,
        timeout_seconds: float | None = None,
    ) -> GraphQueryResult:
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError("entity_id is required")
        if direction not in {"in", "out", "both"}:
            raise ValueError("direction must be in, out or both")
        self._positive_int(depth, "depth", 5)
        self._positive_int(limit, "limit", 500)
        scope = self._scope(allowed_project_ids)
        if isinstance(relation_types, (str, bytes)):
            raise ValueError("relation_types must be a sequence of relation type names")
        allowed_types = frozenset(relation_types)
        if any(not isinstance(item, str) or not item.strip() for item in allowed_types):
            raise ValueError("relation_types must contain non-empty strings")

        async def run() -> GraphQueryResult:
            root = await self.repository.get_entity(entity_id, allowed_project_ids=scope)
            if root is None:
                return GraphQueryResult((), (), False, 0)
            entities: dict[str, Any] = {entity_id: root}
            relations: dict[str, Any] = {}
            frontier = [entity_id]
            truncated = False
            for _ in range(depth):
                next_frontier: list[str] = []
                for current in frontier:
                    remaining = limit - len(relations)
                    if remaining <= 0:
                        truncated = True
                        break
                    edges = await self.repository.list_relations(
                        current, direction=direction, allowed_project_ids=scope,
                        limit=min(500, remaining + 1),
                    )
                    if len(edges) > remaining:
                        truncated = True
                        edges = edges[:remaining]
                    for edge_record in edges:
                        edge = self._relation(edge_record)
                        source, target, relation_id = self._relation_endpoints(edge)
                        if allowed_types and edge.relation_type not in allowed_types:
                            continue
                        relations.setdefault(relation_id, edge_record)
                        adjacent = target if direction == "out" or (
                            direction == "both" and source == current
                        ) else source
                        if adjacent not in entities:
                            if len(entities) >= limit + 1:
                                truncated = True
                                continue
                            node = await self.repository.get_entity(
                                adjacent, allowed_project_ids=scope
                            )
                            if node is not None:
                                entities[adjacent] = node
                                next_frontier.append(adjacent)
                if truncated and len(relations) >= limit:
                    break
                frontier = list(dict.fromkeys(next_frontier))
                if not frontier:
                    break
            if len(entities) > limit:
                truncated = True
                entities = dict(list(entities.items())[:limit])
            return GraphQueryResult(
                tuple(entities.values()), tuple(relations.values()), truncated,
                len(entities),
            )

        return await asyncio.wait_for(run(), timeout=self._timeout(timeout_seconds))

    async def shortest_path(
        self, source_entity_id: str, target_entity_id: str, *,
        relation_types: Sequence[str] = (), max_depth: int = 5,
        allowed_project_ids: Sequence[str] = (), timeout_seconds: float | None = None,
    ) -> GraphPath | None:
        for name, value in (
            ("source_entity_id", source_entity_id),
            ("target_entity_id", target_entity_id),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        self._positive_int(max_depth, "max_depth", 8)
        scope = self._scope(allowed_project_ids)
        if isinstance(relation_types, (str, bytes)):
            raise ValueError("relation_types must be a sequence of relation type names")
        allowed_types = frozenset(relation_types)
        if any(not isinstance(item, str) or not item.strip() for item in allowed_types):
            raise ValueError("relation_types must contain non-empty strings")

        async def run() -> GraphPath | None:
            source = await self.repository.get_entity(source_entity_id, allowed_project_ids=scope)
            target = await self.repository.get_entity(target_entity_id, allowed_project_ids=scope)
            if source is None or target is None:
                return None
            if source_entity_id == target_entity_id:
                return GraphPath((source_entity_id,), ())
            queue: list[tuple[str, tuple[str, ...], tuple[str, ...]]] = [
                (source_entity_id, (source_entity_id,), ())
            ]
            visited = {source_entity_id}
            visit_budget = 5000
            while queue and len(visited) <= visit_budget:
                current, path_nodes, path_edges = queue.pop(0)
                if len(path_edges) >= max_depth:
                    continue
                edges = await self.repository.list_relations(
                    current, direction="out", allowed_project_ids=scope, limit=500
                )
                for edge_record in edges:
                    edge = self._relation(edge_record)
                    start, end, relation_id = self._relation_endpoints(edge)
                    if start != current or (
                        allowed_types and edge.relation_type not in allowed_types
                    ):
                        continue
                    if end == target_entity_id:
                        return GraphPath(path_nodes + (end,), path_edges + (relation_id,))
                    if end not in visited:
                        visited.add(end)
                        queue.append((end, path_nodes + (end,), path_edges + (relation_id,)))
                        if len(visited) > visit_budget:
                            return None
            return None

        return await asyncio.wait_for(run(), timeout=self._timeout(timeout_seconds))

    async def match_relation_chain(
        self, start_entity_id: str, relation_types: Sequence[str], *,
        allowed_project_ids: Sequence[str] = (), timeout_seconds: float | None = None,
    ) -> tuple[GraphPath, ...]:
        if not isinstance(start_entity_id, str) or not start_entity_id.strip():
            raise ValueError("start_entity_id is required")
        if (
            isinstance(relation_types, (str, bytes))
            or not relation_types
            or len(relation_types) > 8
            or any(
                not isinstance(value, str) or not value.strip()
                for value in relation_types
            )
        ):
            raise ValueError("relation_types must contain 1-8 relation type names")
        scope = self._scope(allowed_project_ids)

        async def run() -> tuple[GraphPath, ...]:
            if await self.repository.get_entity(start_entity_id, allowed_project_ids=scope) is None:
                return ()
            paths = [GraphPath((start_entity_id,), ())]
            for relation_type in relation_types:
                next_paths: list[GraphPath] = []
                for path in paths:
                    edges = await self.repository.list_relations(
                        path.entity_ids[-1], direction="out",
                        allowed_project_ids=scope, limit=500,
                    )
                    for edge_record in edges:
                        edge = self._relation(edge_record)
                        source, target, relation_id = self._relation_endpoints(edge)
                        if source == path.entity_ids[-1] and edge.relation_type == relation_type:
                            next_paths.append(GraphPath(
                                path.entity_ids + (target,), path.relation_ids + (relation_id,)
                            ))
                            if len(next_paths) >= 500:
                                break
                    if len(next_paths) >= 500:
                        break
                paths = next_paths
                if not paths:
                    break
            return tuple(paths)

        return await asyncio.wait_for(run(), timeout=self._timeout(timeout_seconds))

    async def hybrid_retrieve(
        self, query: str, *, document_retriever: AclAwareDocumentRetriever,
        allowed_project_ids: Sequence[str] = (), graph_seed_limit: int = 50,
        result_limit: int = 20, timeout_seconds: float | None = None,
    ) -> GraphHybridResult:
        if not isinstance(query, str) or not query.strip() or len(query) > 4096:
            raise ValueError("query must contain 1-4096 non-whitespace characters")
        self._positive_int(graph_seed_limit, "graph_seed_limit", 200)
        self._positive_int(result_limit, "result_limit", 100)
        scope = self._scope(allowed_project_ids)

        async def run() -> GraphHybridResult:
            seeds = await self.repository.search_entities(
                query, allowed_project_ids=scope, limit=graph_seed_limit
            )
            entity_ids: dict[str, Any] = {}
            candidate_ids: list[str] = []
            for record in seeds:
                entity = self._entity(record)
                entity_id = getattr(entity, "entity_id", None)
                if isinstance(entity_id, str):
                    entity_ids.setdefault(entity_id, record)
                source_ref = getattr(record, "source_ref", None)
                if source_ref is not None and source_ref.domain is GraphSourceDomain.KNOWLEDGE:
                    if (
                        source_ref.source_type == "document"
                        and source_ref.source_id not in candidate_ids
                    ):
                        candidate_ids.append(source_ref.source_id)
            graph = GraphQueryResult(tuple(entity_ids.values()), (), False, len(entity_ids))
            if not candidate_ids:
                return GraphHybridResult(graph, (), ())
            hits = await document_retriever.retrieve(
                query=query, candidate_document_ids=tuple(candidate_ids),
                allowed_project_ids=scope, limit=result_limit,
            )
            # The document retriever owns canonical ACL re-checking; constrain its output
            # to graph-derived candidates as defense in depth.
            candidate_set = set(candidate_ids)
            safe_hits = tuple(
                hit for hit in hits
                if getattr(hit, "document_id", None) in candidate_set
            )[:result_limit]
            return GraphHybridResult(graph, safe_hits, tuple(candidate_ids))

        return await asyncio.wait_for(run(), timeout=self._timeout(timeout_seconds))
