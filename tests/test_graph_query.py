from __future__ import annotations

import asyncio
import unittest
from dataclasses import dataclass

from agent_platform.graph.query import GraphQueryService
from agent_platform.graph.schema import GraphEntity, GraphRelation, GraphSourceDomain


@dataclass(frozen=True)
class EntityRecord:
    entity: GraphEntity
    name: str
    source_ref: GraphSourceReference | None = None


@dataclass(frozen=True)
class RelationRecord:
    relation: GraphRelation


@dataclass(frozen=True)
class GraphSourceReference:
    domain: GraphSourceDomain
    source_type: str
    source_id: str
    source_version: int


@dataclass(frozen=True)
class Hit:
    document_id: str
    score: float


class FakeGraphRepository:
    def __init__(self):
        self.tenant_id = "tenant-a"
        self.entities = {
            key: EntityRecord(GraphEntity("tenant-a", key, "document", {}, "project-a"), key)
            for key in ("a", "b", "c", "d")
        }
        self.relations = [
            RelationRecord(
                GraphRelation(
                    "tenant-a", "ab", "references", "a", "b",
                    "document", "document", {}, "project-a",
                )
            ),
            RelationRecord(
                GraphRelation(
                    "tenant-a", "bc", "references", "b", "c",
                    "document", "document", {}, "project-a",
                )
            ),
            RelationRecord(
                GraphRelation(
                    "tenant-a", "bd", "mentions", "b", "d",
                    "document", "document", {}, "project-a",
                )
            ),
        ]
        self.calls = []

    async def get_entity(self, entity_id, *, allowed_project_ids=()):
        self.calls.append(("get", entity_id, tuple(allowed_project_ids)))
        if "project-a" not in allowed_project_ids:
            return None
        return self.entities.get(entity_id)

    async def list_relations(
        self, entity_id, *, direction="both", allowed_project_ids=(), limit=200
    ):
        self.calls.append(("edges", entity_id, direction, tuple(allowed_project_ids), limit))
        if "project-a" not in allowed_project_ids:
            return []
        rows = [
            row for row in self.relations
            if (direction in {"out", "both"} and row.relation.source_entity_id == entity_id)
            or (direction in {"in", "both"} and row.relation.target_entity_id == entity_id)
        ]
        return rows[:limit]

    async def search_entities(self, query, *, entity_type=None, allowed_project_ids=(), limit=50):
        self.calls.append(("search", query, tuple(allowed_project_ids), limit))
        if "project-a" not in allowed_project_ids:
            return []
        record = self.entities["a"]
        return [record]


class SlowGraphRepository(FakeGraphRepository):
    async def get_entity(self, entity_id, *, allowed_project_ids=()):
        await asyncio.sleep(0.05)
        return await super().get_entity(entity_id, allowed_project_ids=allowed_project_ids)


class GraphQueryServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repository = FakeGraphRepository()
        self.service = GraphQueryService(tenant_id="tenant-a", repository=self.repository)

    async def test_neighbors_expand_with_direction_and_project_scope(self):
        result = await self.service.neighbors(
            "a", direction="out", depth=2, allowed_project_ids=("project-a",), limit=10
        )
        self.assertEqual({item.entity.entity_id for item in result.entities}, {"a", "b", "c", "d"})
        relation_ids = {item.relation.relation_id for item in result.relations}
        self.assertEqual(relation_ids, {"ab", "bc", "bd"})
        self.assertFalse(result.truncated)
        for call in self.repository.calls:
            if call[0] == "get":
                self.assertEqual(call[2], ("project-a",))
            elif call[0] == "edges":
                self.assertEqual(call[3], ("project-a",))

    async def test_neighbor_limit_reports_truncation_and_deduplicates(self):
        result = await self.service.neighbors(
            "a", depth=2, allowed_project_ids=("project-a",), limit=2
        )
        self.assertTrue(result.truncated)
        self.assertLessEqual(len(result.entities), 2)
        self.assertLessEqual(len(result.relations), 2)
        relation_ids = {item.relation.relation_id for item in result.relations}
        self.assertEqual(len(relation_ids), len(result.relations))

    async def test_shortest_path_returns_minimum_hop_path(self):
        path = await self.service.shortest_path(
            "a", "c", allowed_project_ids=("project-a",), max_depth=3
        )
        self.assertEqual(path.entity_ids, ("a", "b", "c"))
        self.assertEqual(path.relation_ids, ("ab", "bc"))

    async def test_shortest_path_missing_node_and_same_node(self):
        self.assertIsNone(await self.service.shortest_path(
            "a", "missing", allowed_project_ids=("project-a",)
        ))
        path = await self.service.shortest_path(
            "a", "a", allowed_project_ids=("project-a",)
        )
        self.assertEqual(path.entity_ids, ("a",))
        self.assertEqual(path.relation_ids, ())

    async def test_relation_chain_template_matches_ordered_types(self):
        paths = await self.service.match_relation_chain(
            "a", ("references", "references"), allowed_project_ids=("project-a",)
        )
        self.assertEqual(len(paths), 1)
        self.assertEqual(paths[0].entity_ids, ("a", "b", "c"))

    async def test_query_timeout_is_enforced(self):
        service = GraphQueryService(
            tenant_id="tenant-a", repository=SlowGraphRepository()
        )
        with self.assertRaises(asyncio.TimeoutError):
            await service.neighbors(
                "a", allowed_project_ids=("project-a",), timeout_seconds=0.001
            )

    async def test_invalid_limits_and_directions_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "limit"):
            await self.service.neighbors("a", allowed_project_ids=("project-a",), limit=0)
        with self.assertRaisesRegex(ValueError, "direction"):
            await self.service.neighbors(
                "a", direction="sideways", allowed_project_ids=("project-a",)
            )

    async def test_repository_tenant_must_match_service_tenant(self):
        repository = FakeGraphRepository()
        repository.tenant_id = "tenant-b"
        with self.assertRaisesRegex(ValueError, "repository tenant"):
            GraphQueryService(tenant_id="tenant-a", repository=repository)

    async def test_project_scope_is_mandatory_for_project_entities(self):
        result = await self.service.neighbors("a", allowed_project_ids=())
        self.assertEqual(result.entities, ())
        self.assertEqual(result.relations, ())

    async def test_query_service_rejects_repository_without_tenant_binding(self):
        with self.assertRaisesRegex(ValueError, "explicitly bound"):
            GraphQueryService(tenant_id="tenant-a", repository=object())

    async def test_hybrid_query_respects_postgres_search_length_bound(self):
        class Retriever:
            async def retrieve(self, **kwargs):
                raise AssertionError("retriever must not run for invalid query")

        with self.assertRaisesRegex(ValueError, "1-200"):
            await self.service.hybrid_retrieve(
                "x" * 201, document_retriever=Retriever(),
                allowed_project_ids=("project-a",),
            )

    async def test_rejects_repository_entity_outside_tenant_or_project_scope(self):
        class CrossTenantRepository(FakeGraphRepository):
            async def get_entity(self, entity_id, *, allowed_project_ids=()):
                if entity_id == "a":
                    return EntityRecord(
                        GraphEntity("tenant-b", "a", "document", {}, "project-a"), "a"
                    )
                return await super().get_entity(
                    entity_id, allowed_project_ids=allowed_project_ids
                )

        service = GraphQueryService(
            tenant_id="tenant-a", repository=CrossTenantRepository()
        )
        with self.assertRaisesRegex(PermissionError, "outside the tenant"):
            await service.neighbors("a", allowed_project_ids=("project-a",))

    async def test_shortest_path_does_not_disclose_hidden_intermediate_nodes(self):
        class HiddenIntermediateRepository(FakeGraphRepository):
            async def get_entity(self, entity_id, *, allowed_project_ids=()):
                if entity_id == "b":
                    return EntityRecord(
                        GraphEntity("tenant-a", "b", "document", {}, "secret-project"), "b"
                    )
                return await super().get_entity(
                    entity_id, allowed_project_ids=allowed_project_ids
                )

        service = GraphQueryService(
            tenant_id="tenant-a", repository=HiddenIntermediateRepository()
        )
        with self.assertRaisesRegex(PermissionError, "outside project scope"):
            await service.shortest_path(
                "a", "c", allowed_project_ids=("project-a",)
            )

    async def test_hybrid_retrieval_rechecks_acl_and_filters_unrelated_hits(self):
        record = self.repository.entities["a"]
        self.repository.entities["a"] = EntityRecord(
            record.entity, record.name,
            GraphSourceReference(GraphSourceDomain.KNOWLEDGE, "document", "doc-a", 1),
        )

        class Retriever:
            async def retrieve(self, *, query, candidate_document_ids, allowed_project_ids, limit):
                self.assertion = (
                    query, tuple(candidate_document_ids),
                    tuple(allowed_project_ids), limit,
                )
                return (Hit("doc-a", 0.9), Hit("doc-secret", 1.0))

        retriever = Retriever()
        result = await self.service.hybrid_retrieve(
            "Ada", document_retriever=retriever,
            allowed_project_ids=("project-a",), result_limit=5,
        )
        self.assertEqual(result.candidate_document_ids, ("doc-a",))
        self.assertEqual(tuple(hit.document_id for hit in result.document_hits), ("doc-a",))
        self.assertEqual(retriever.assertion[2], ("project-a",))


if __name__ == "__main__":
    unittest.main()
