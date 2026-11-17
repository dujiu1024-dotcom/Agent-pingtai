from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.graph import create_graph_query_router
from agent_platform.graph.query import GraphQueryService
from agent_platform.knowledge.contracts import KnowledgePrincipal


class _Repository:
    def __init__(self, *, delay=0):
        self.delay = delay

    async def execute_query(self, *, tenant_id, project_ids, spec):
        if self.delay:
            await asyncio.sleep(self.delay)
        return [{"tenant_id": tenant_id, "project_id": None, "entity_id": "entity-1"}]


def _client(*, allowed=True, delay=0):
    principal = KnowledgePrincipal(
        tenant_id="tenant-a", user_id="user-a",
        project_ids=frozenset({"project-a"}),
    )

    async def resolve(_request):
        return principal

    async def authorize(_principal):
        return allowed

    app = FastAPI()
    app.include_router(create_graph_query_router(
        service=GraphQueryService(_Repository(delay=delay)),
        resolve_principal=resolve,
        authorize_query=authorize,
    ))
    return TestClient(app)


def test_graph_query_http_route_returns_bounded_results():
    response = _client().post("/api/platform/v1/graph/query", json={
        "mode": "neighbors",
        "start_entity_ids": ["root-1"],
        "limit": 10,
    })
    assert response.status_code == 200
    assert response.json()["items"][0]["entity_id"] == "entity-1"


def test_graph_query_http_route_rejects_caller_tenant_override():
    response = _client().post("/api/platform/v1/graph/query", json={
        "start_entity_ids": ["root-1"],
        "tenant_id": "tenant-victim",
    })
    assert response.status_code == 422


def test_graph_query_http_route_requires_authorization():
    response = _client(allowed=False).post(
        "/api/platform/v1/graph/query",
        json={"start_entity_ids": ["root-1"]},
    )
    assert response.status_code == 403


def test_hybrid_http_route_requires_exact_query_and_graph_fields():
    response = _client().post("/api/platform/v1/graph/hybrid", json={
        "query": "find related items",
        "graph": {"start_entity_ids": ["root-1"]},
        "tenant_id": "tenant-victim",
    })
    assert response.status_code == 422


def test_graph_query_http_route_maps_timeout_to_gateway_timeout():
    response = _client(delay=0.2).post(
        "/api/platform/v1/graph/query",
        json={"start_entity_ids": ["root-1"], "timeout_ms": 100},
    )
    assert response.status_code == 504
