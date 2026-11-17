from __future__ import annotations

import unittest
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.graph import create_graph_router
from agent_platform.graph.query import GraphQueryResult
from agent_platform.identity.models import Principal


class FakeGraphService:
    tenant_id = "tenant-a"

    def __init__(self):
        a = SimpleNamespace(
            tenant_id="tenant-a", entity_id="a", entity_type="person",
            attributes={"label": "Ada"}, project_id="project-a",
        )
        b = SimpleNamespace(
            tenant_id="tenant-a", entity_id="b", entity_type="concept",
            attributes={"label": "Graph"}, project_id="project-a",
        )
        relation = SimpleNamespace(
            tenant_id="tenant-a", relation_id="r1", relation_type="knows",
            source_entity_id="a", target_entity_id="b", project_id="project-a",
            attributes={},
        )
        self.entities = [
            SimpleNamespace(entity=a, name="Ada", source_ref=None),
            SimpleNamespace(entity=b, name="Graph", source_ref=None),
        ]
        self.relations = [SimpleNamespace(relation=relation)]

    @staticmethod
    def _scope(values):
        return tuple(values)

    def _validate_entity(self, record, entity_id, scope):
        entity = getattr(record, "entity", record)
        if entity.tenant_id != self.tenant_id or entity.project_id not in scope:
            raise PermissionError("scope denied")
        return entity

    def _validate_relation(self, record, scope):
        relation = getattr(record, "relation", record)
        if relation.tenant_id != self.tenant_id or relation.project_id not in scope:
            raise PermissionError("scope denied")
        return relation

    async def neighbors(self, entity_id, **kwargs):
        return GraphQueryResult(tuple(self.entities), tuple(self.relations), False, 2)


class GraphAdminApiTests(unittest.TestCase):
    def setUp(self):
        self.service = FakeGraphService()
        self.principal = Principal(
            user_id="user-a", tenant_id="tenant-a", session_id="session-a",
            email="a@example.com", display_name="Ada",
        )

        async def resolve_principal(request):
            return self.principal

        async def resolve_query_service(principal):
            return self.service

        async def authorize_read(principal):
            return ("project-a",)

        self.app = FastAPI()
        self.app.include_router(create_graph_router(
            resolve_principal=resolve_principal,
            resolve_query_service=resolve_query_service,
            authorize_read=authorize_read,
        ))
        self.client = TestClient(self.app)

    def test_search_returns_tenant_scoped_entities(self):
        self.service.repository = SimpleNamespace(
            search_entities=lambda *args, **kwargs: self.service.entities
        )

        async def search_entities(*args, **kwargs):
            return self.service.entities

        self.service.repository.search_entities = search_entities
        response = self.client.get("/api/platform/v1/graph/entities?q=Ada")
        self.assertEqual(response.status_code, 200)
        self.assertEqual([item["entity_id"] for item in response.json()["items"]], ["a", "b"])

    def test_neighbors_and_metrics_are_project_scoped(self):
        async def search_entities(*args, **kwargs):
            return self.service.entities

        async def list_relations(*args, **kwargs):
            return self.service.relations

        self.service.repository = SimpleNamespace(
            search_entities=search_entities, list_relations=list_relations,
        )
        neighbors = self.client.get(
            "/api/platform/v1/graph/entities/a/neighbors?direction=both&depth=1"
        )
        self.assertEqual(neighbors.status_code, 200)
        self.assertEqual(len(neighbors.json()["relations"]), 1)
        metrics = self.client.get("/api/platform/v1/graph/metrics?q=Ada")
        self.assertEqual(metrics.status_code, 200)
        self.assertEqual(metrics.json()["entity_count"], 2)
        self.assertEqual(metrics.json()["relation_count"], 1)
        self.assertEqual(metrics.json()["isolated_entity_count"], 0)
        self.assertEqual(metrics.json()["active_subgraph_count"], 1)
        self.assertEqual(metrics.json()["scope"], "authorized_search_sample")

    def test_search_rejects_empty_query_and_service_tenant_mismatch(self):
        self.assertEqual(
            self.client.get("/api/platform/v1/graph/entities?q=").status_code, 400
        )

        async def wrong_service(principal):
            return SimpleNamespace(tenant_id="tenant-b")

        async def resolve_principal(request):
            return self.principal

        async def authorize_read(principal):
            return ()

        app = FastAPI()
        app.include_router(create_graph_router(
            resolve_principal=resolve_principal,
            resolve_query_service=wrong_service,
            authorize_read=authorize_read,
        ))
        response = TestClient(app).get("/api/platform/v1/graph/entities?q=A")
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
