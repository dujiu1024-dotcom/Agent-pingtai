from __future__ import annotations

import asyncio
import unittest

from agent_platform.graph.query import (
    GraphQueryAuthorizationError,
    GraphQueryError,
    GraphQueryMode,
    GraphQueryService,
    GraphQuerySpec,
    GraphQueryTimeout,
)
from agent_platform.knowledge.contracts import KnowledgePrincipal


class _Repository:
    def __init__(self, rows=None, *, delay=0):
        self.rows = rows or []
        self.delay = delay
        self.calls = []

    async def execute_query(self, *, tenant_id, project_ids, spec):
        self.calls.append((tenant_id, project_ids, spec))
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.rows


class _VectorRetriever:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.calls = []

    async def retrieve(self, *, principal, query, candidate_entity_ids, limit):
        self.calls.append((principal, query, candidate_entity_ids, limit))
        return self.rows


def _principal():
    return KnowledgePrincipal(
        tenant_id="tenant-a",
        user_id="user-1",
        project_ids=frozenset({"project-read"}),
        manage_project_ids=frozenset({"project-manage"}),
    )


def _spec(**overrides):
    values = {
        "mode": GraphQueryMode.NEIGHBORS,
        "start_entity_ids": ("entity-root",),
        "relation_types": ("references",),
        "max_depth": 2,
        "limit": 2,
        "timeout_ms": 500,
    }
    values.update(overrides)
    return GraphQuerySpec(**values)


class GraphQuerySpecTests(unittest.TestCase):
    def test_parse_valid_query_dsl(self):
        spec = GraphQuerySpec.from_payload({
            "mode": "paths",
            "start_entity_ids": ["root-1", "root-2"],
            "relation_types": ["depends_on"],
            "entity_types": ["document"],
            "max_depth": 4,
            "limit": 25,
            "timeout_ms": 1200,
        })
        self.assertIs(spec.mode, GraphQueryMode.PATHS)
        self.assertEqual(spec.max_depth, 4)
        self.assertEqual(spec.start_entity_ids, ("root-1", "root-2"))

    def test_reject_tenant_project_and_unknown_fields_from_payload(self):
        for field in ("tenant_id", "project_ids", "user_id", "raw_sql"):
            with self.subTest(field=field), self.assertRaises(GraphQueryError):
                GraphQuerySpec.from_payload({
                    "start_entity_ids": ["entity-1"], field: "attacker-controlled",
                })

    def test_reject_invalid_depth_limit_timeout_and_duplicate_ids(self):
        for kwargs in (
            {"max_depth": True},
            {"max_depth": 6},
            {"limit": 0},
            {"limit": 101},
            {"timeout_ms": 99},
            {"timeout_ms": 5001},
            {"start_entity_ids": ("same", "same")},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(GraphQueryError):
                _spec(**kwargs)

    def test_reject_noncanonical_identifiers(self):
        with self.assertRaises(GraphQueryError):
            _spec(start_entity_ids=("../other",))
        with self.assertRaises(GraphQueryError):
            _spec(relation_types=("contains/path",))


class GraphQueryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_forwards_principal_scope_and_deduplicates(self):
        repo = _Repository([
            {"tenant_id": "tenant-a", "project_id": None, "entity_id": "e1"},
            {"tenant_id": "tenant-a", "project_id": "project-read", "entity_id": "e2"},
            {"tenant_id": "tenant-a", "project_id": "project-read", "entity_id": "e2"},
        ])
        service = GraphQueryService(repo)
        result = await service.query(principal=_principal(), spec=_spec())
        self.assertEqual(len(repo.calls), 1)
        tenant, projects, spec = repo.calls[0]
        self.assertEqual(tenant, "tenant-a")
        self.assertEqual(projects, frozenset({"project-read", "project-manage"}))
        self.assertEqual([item["entity_id"] for item in result.items], ["e1", "e2"])
        self.assertFalse(result.truncated)

    async def test_query_truncates_to_limit(self):
        repo = _Repository([
            {"tenant_id": "tenant-a", "project_id": None, "entity_id": f"e{i}"}
            for i in range(4)
        ])
        result = await GraphQueryService(repo).query(
            principal=_principal(), spec=_spec(limit=2)
        )
        self.assertEqual(len(result.items), 2)
        self.assertTrue(result.truncated)

    async def test_query_rejects_cross_tenant_and_unauthorized_project_rows(self):
        for row in (
            {"tenant_id": "tenant-b", "project_id": None, "entity_id": "e1"},
            {"tenant_id": "tenant-a", "project_id": "project-secret", "entity_id": "e1"},
        ):
            with self.subTest(row=row):
                with self.assertRaises(GraphQueryAuthorizationError):
                    await GraphQueryService(_Repository([row])).query(
                        principal=_principal(), spec=_spec()
                    )


    async def test_paths_reject_unauthorized_intermediate_nodes(self):
        repo = _Repository([
            {
                "tenant_id": "tenant-a", "project_id": None, "entity_id": "end",
                "result_id": "path-1",
                "path": [
                    {"tenant_id": "tenant-a", "project_id": None, "entity_id": "start"},
                    {"tenant_id": "tenant-a", "project_id": "project-secret", "entity_id": "middle"},
                    {"tenant_id": "tenant-a", "project_id": None, "entity_id": "end"},
                ],
            }
        ])
        with self.assertRaises(GraphQueryAuthorizationError):
            await GraphQueryService(repo).query(
                principal=_principal(), spec=_spec(mode=GraphQueryMode.PATHS)
            )

    async def test_query_timeout_is_fail_closed(self):
        repo = _Repository(delay=0.2)
        with self.assertRaises(GraphQueryTimeout):
            await GraphQueryService(repo).query(
                principal=_principal(), spec=_spec(timeout_ms=100)
            )

    async def test_query_requires_authenticated_principal(self):
        with self.assertRaises(GraphQueryAuthorizationError):
            await GraphQueryService(_Repository()).query(
                principal=None, spec=_spec()  # type: ignore[arg-type]
            )

    async def test_hybrid_search_fuses_and_deduplicates_graph_and_vector_hits(self):
        graph = _Repository([
            {"tenant_id": "tenant-a", "project_id": None, "entity_id": "e1", "label": "graph"},
            {"tenant_id": "tenant-a", "project_id": "project-read", "entity_id": "e2", "label": "graph"},
        ])
        vector = _VectorRetriever([
            {"tenant_id": "tenant-a", "project_id": "project-read", "entity_id": "e2", "label": "vector"},
            {"tenant_id": "tenant-a", "project_id": None, "entity_id": "e1", "label": "vector"},
        ])
        result = await GraphQueryService(graph, vector_retriever=vector).hybrid_search(
            principal=_principal(), query="documents that depend on X", spec=_spec()
        )
        self.assertEqual(vector.calls[0][2], ("e1", "e2"))
        self.assertEqual({item["entity_id"] for item in result.items}, {"e1", "e2"})
        self.assertTrue(all(item["graph"] and item["vector"] for item in result.items))

    async def test_hybrid_search_does_not_run_vector_without_graph_candidates(self):
        vector = _VectorRetriever([{"tenant_id": "tenant-a", "project_id": None, "entity_id": "e1"}])
        result = await GraphQueryService(
            _Repository([]), vector_retriever=vector
        ).hybrid_search(principal=_principal(), query="query", spec=_spec())
        self.assertEqual(result.items, ())
        self.assertEqual(vector.calls, [])

    async def test_hybrid_search_rejects_vector_result_outside_graph_candidate_set(self):
        vector = _VectorRetriever([
            {"tenant_id": "tenant-a", "project_id": None, "entity_id": "not-a-candidate"}
        ])
        with self.assertRaises(GraphQueryAuthorizationError):
            await GraphQueryService(
                _Repository([
                    {"tenant_id": "tenant-a", "project_id": None, "entity_id": "e1"}
                ]),
                vector_retriever=vector,
            ).hybrid_search(principal=_principal(), query="query", spec=_spec())

    async def test_hybrid_search_rejects_unauthorized_vector_project(self):
        vector = _VectorRetriever([
            {"tenant_id": "tenant-a", "project_id": "project-secret", "entity_id": "e1"}
        ])
        with self.assertRaises(GraphQueryAuthorizationError):
            await GraphQueryService(
                _Repository([
                    {"tenant_id": "tenant-a", "project_id": None, "entity_id": "e1"}
                ]),
                vector_retriever=vector,
            ).hybrid_search(principal=_principal(), query="query", spec=_spec())


if __name__ == "__main__":
    unittest.main()
