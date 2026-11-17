"""Authenticated knowledge-graph browsing, quality metrics and annotation API."""
from __future__ import annotations

from typing import Any, Awaitable, Callable, Sequence

from fastapi import APIRouter, HTTPException, Request

from agent_platform.graph.query import GraphQueryService
from agent_platform.identity.models import Principal

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
QueryServiceResolver = Callable[[Principal], Awaitable[GraphQueryService]]
GraphReadAuthorizer = Callable[[Principal], Awaitable[Sequence[str]]]
GovernanceResolver = Callable[[Principal], Awaitable[Any]]


def create_graph_router(
    *,
    resolve_principal: PrincipalResolver,
    resolve_query_service: QueryServiceResolver,
    authorize_read: GraphReadAuthorizer,
    resolve_governance: GovernanceResolver | None = None,
    resolve_knowledge_principal: Callable[[Request], Awaitable[Any]] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/graph", tags=["knowledge-graph"])

    async def context(request: Request):
        principal = await resolve_principal(request)
        allowed = await authorize_read(principal)
        if isinstance(allowed, (str, bytes)):
            raise HTTPException(403, "graph project scope must be an authorized project list")
        allowed_projects = tuple(allowed)
        service = await resolve_query_service(principal)
        if getattr(service, "tenant_id", None) != principal.tenant_id:
            raise HTTPException(403, "graph query service tenant mismatch")
        try:
            allowed_projects = service._scope(allowed_projects)
        except (AttributeError, ValueError) as exc:
            raise HTTPException(403, "graph project scope is invalid") from exc
        return principal, service, allowed_projects

    def entity_payload(record: Any) -> dict[str, Any]:
        entity = getattr(record, "entity", record)
        source = getattr(record, "source_ref", None)
        return {
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "name": getattr(record, "name", entity.entity_id),
            "attributes": dict(entity.attributes),
            "project_id": entity.project_id,
            "source_ref": ({
                "domain": source.domain.value,
                "source_type": source.source_type,
                "source_id": source.source_id,
                "source_version": source.source_version,
            } if source else None),
        }

    def relation_payload(record: Any) -> dict[str, Any]:
        relation = getattr(record, "relation", record)
        return {
            "relation_id": relation.relation_id,
            "relation_type": relation.relation_type,
            "source_entity_id": relation.source_entity_id,
            "target_entity_id": relation.target_entity_id,
            "project_id": relation.project_id,
            "attributes": dict(relation.attributes),
        }

    @router.get("/entities")
    async def search_entities(
        request: Request, q: str, entity_type: str | None = None, limit: int = 50,
    ):
        if not isinstance(q, str) or not q.strip() or len(q) > 200:
            raise HTTPException(400, "q must contain 1-200 non-whitespace characters")
        if not 1 <= limit <= 100:
            raise HTTPException(400, "limit must be between 1 and 100")
        _, service, allowed = await context(request)
        try:
            items = await service.repository.search_entities(
                q.strip(), entity_type=entity_type, allowed_project_ids=allowed, limit=limit,
            )
            for item in items:
                entity = getattr(item, "entity", item)
                service._validate_entity(item, entity.entity_id, allowed)
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"items": [entity_payload(item) for item in items], "limit": limit}

    @router.get("/entities/{entity_id}/neighbors")
    async def entity_neighbors(
        entity_id: str, request: Request, direction: str = "both",
        depth: int = 1, limit: int = 100,
    ):
        _, service, allowed = await context(request)
        try:
            result = await service.neighbors(
                entity_id, direction=direction, depth=depth,
                allowed_project_ids=allowed, limit=limit,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "entities": [entity_payload(item) for item in result.entities],
            "relations": [relation_payload(item) for item in result.relations],
            "truncated": result.truncated,
            "visited_count": result.visited_count,
        }

    @router.get("/metrics")
    async def graph_metrics(request: Request, q: str, limit: int = 100):
        if not isinstance(q, str) or not q.strip() or len(q) > 200:
            raise HTTPException(400, "q must contain 1-200 non-whitespace characters")
        if not 1 <= limit <= 100:
            raise HTTPException(400, "limit must be between 1 and 100")
        _, service, allowed = await context(request)
        try:
            records = await service.repository.search_entities(
                q.strip(), allowed_project_ids=allowed, limit=limit,
            )
            entities = [service._validate_entity(
                item, getattr(getattr(item, "entity", item), "entity_id"), allowed
            ) for item in records]
            ids = {item.entity_id for item in entities}
            relations: dict[str, Any] = {}
            for entity_id in ids:
                for record in await service.repository.list_relations(
                    entity_id, direction="both", allowed_project_ids=allowed, limit=500,
                ):
                    relation = service._validate_relation(record, allowed)
                    if (
                        relation.source_entity_id in ids
                        and relation.target_entity_id in ids
                    ):
                        relations[relation.relation_id] = relation
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        degree = {entity_id: 0 for entity_id in ids}
        adjacency = {entity_id: set() for entity_id in ids}
        for relation in relations.values():
            degree[relation.source_entity_id] += 1
            degree[relation.target_entity_id] += 1
            adjacency[relation.source_entity_id].add(relation.target_entity_id)
            adjacency[relation.target_entity_id].add(relation.source_entity_id)
        isolated = sum(1 for value in degree.values() if value == 0)
        components = 0
        unseen = set(ids)
        while unseen:
            components += 1
            stack = [unseen.pop()]
            while stack:
                for neighbor in adjacency[stack.pop()] & unseen:
                    unseen.remove(neighbor)
                    stack.append(neighbor)
        count = len(ids)
        possible_edges = count * (count - 1)
        return {
            "scope": "authorized_search_sample",
            "query": q.strip(),
            "sampled": count,
            "sample_limit": limit,
            "truncated": count == limit,
            "entity_count": count,
            "relation_count": len(relations),
            "isolated_entity_count": isolated,
            "isolated_entity_ratio": isolated / count if count else 0.0,
            "relation_density": len(relations) / possible_edges if possible_edges else 0.0,
            "active_subgraph_count": components,
        }

    @router.patch("/entities/{entity_id}")
    async def correct_entity(entity_id: str, request: Request):
        if resolve_governance is None or resolve_knowledge_principal is None:
            raise HTTPException(503, "graph annotation is not configured")
        principal = await resolve_principal(request)
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "valid JSON body required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object required")
        version, attributes = body.get("expected_version"), body.get("attributes")
        if type(version) is not int or version < 1 or not isinstance(attributes, dict):
            raise HTTPException(400, "expected_version and attributes are required")
        service = await resolve_governance(principal)
        knowledge_principal = await resolve_knowledge_principal(request)
        try:
            entity = await service.correct_entity(
                principal=knowledge_principal, entity_id=entity_id,
                expected_version=version, attributes=attributes,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "attributes": dict(entity.attributes),
            "project_id": entity.project_id,
            "version": entity.version,
            "status": entity.status.value,
            "updated_by": entity.updated_by,
            "updated_at": entity.updated_at.isoformat(),
        }

    return router
