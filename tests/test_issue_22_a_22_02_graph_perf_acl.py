"""PostgreSQL-scale graph query latency and tenant/project ACL acceptance.

Runs only when AGENT_PLATFORM_TEST_DSN points at an isolated PostgreSQL test DB.
The fixture uses 5k entities and ~5k edges; this is an executable CI benchmark,
not a claim about production hardware or arbitrary graph sizes.
"""
from __future__ import annotations

import os
import time
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")

if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.graph.postgres_repository import PostgresGraphRepository
    from agent_platform.graph.query import GraphQueryService


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class GraphQueryPerformanceAndAclAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = f"graph-perf-{uuid4().hex}"
        self.other_tenant_id = f"graph-other-{uuid4().hex}"
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for migration in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(migration.read_text(encoding="utf-8"), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    "INSERT INTO platform_tenants (tenant_id, display_name) VALUES (%s, %s)",
                    ((self.tenant_id, "Graph performance test"),
                     (self.other_tenant_id, "Graph isolation test")),
                )
            entities = [
                (self.tenant_id, f"n{i:05d}", "document", f"node-{i:05d}",
                 '{"title":"benchmark"}', "project-a")
                for i in range(5_000)
            ]
            entities.extend([
                (self.tenant_id, "secret-x", "document", "secret-x",
                 '{"title":"hidden"}', "project-b"),
                (self.tenant_id, "secret-y", "document", "secret-y",
                 '{"title":"hidden"}', "project-b"),
            ])
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    """INSERT INTO graph_entities
                       (tenant_id, entity_id, entity_type, entity_name, attributes, project_id)
                       VALUES (%s, %s, %s, %s, %s::jsonb, %s)""",
                    entities,
                )
            relations = [
                (self.tenant_id, f"chain-{i:05d}", "references",
                 f"n{i:05d}", f"n{i+1:05d}", "document", "document",
                 "{}", "project-a")
                for i in range(4_999)
            ]
            # Fan-out/fan-in around the root makes the neighbor expansion non-trivial.
            relations.extend(
                (self.tenant_id, f"hub-{i:03d}", "references",
                 "n00000", f"n{i+100:05d}", "document", "document",
                 "{}", "project-a")
                for i in range(24)
            )
            relations.extend(
                (self.tenant_id, f"leaf-{i:03d}", "references",
                 f"n{i+00100:05d}", f"n{i+200:05d}", "document", "document",
                 "{}", "project-a")
                for i in range(24)
            )
            relations.append(
                (self.tenant_id, "secret-edge", "references", "secret-x", "secret-y",
                 "document", "document", "{}", "project-b")
            )
            async with conn.cursor() as cursor:
                await cursor.executemany(
                    """INSERT INTO graph_relations
                       (tenant_id, relation_id, relation_type, source_entity_id,
                        target_entity_id, source_entity_type, target_entity_type,
                        attributes, project_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)""",
                    relations,
                )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.repository = PostgresGraphRepository(
            tenant_id=self.tenant_id, connection_factory=connect,
        )
        self.service = GraphQueryService(
            tenant_id=self.tenant_id, repository=self.repository,
            default_timeout_seconds=2.0,
        )
        self.allowed = ("project-a",)

    async def _measure(self, operation, repeats=5):
        await operation()  # warm connection pools, statement caches and OS pages
        samples = []
        for _ in range(repeats):
            started = time.perf_counter()
            await operation()
            samples.append((time.perf_counter() - started) * 1000)
        return {
            "samples_ms": samples,
            "p50_ms": sorted(samples)[len(samples) // 2],
            "p95_ms": sorted(samples)[min(len(samples) - 1, int((len(samples) - 1) * .95))],
            "max_ms": max(samples),
        }

    async def test_typical_scale_latency_and_tenant_project_isolation(self):
        async def path_query():
            result = await self.service.shortest_path(
                "n00000", "n00005", max_depth=8,
                allowed_project_ids=self.allowed,
            )
            self.assertIsNotNone(result)
            self.assertEqual(len(result.relation_ids), 5)

        async def neighbor_query():
            result = await self.service.neighbors(
                "n00000", direction="out", depth=2, limit=100,
                allowed_project_ids=self.allowed,
            )
            self.assertGreaterEqual(len(result.entities), 25)
            self.assertLessEqual(len(result.entities), 100)
            self.assertTrue(all(
                item.project_id == "project-a" for item in result.entities
            ))

        subgraph_ids = tuple(f"n{i:05d}" for i in range(50))

        async def subgraph_query():
            result = await self.service.match_subgraph(
                subgraph_ids, relation_types=("references",),
                allowed_project_ids=self.allowed, require_connected=True,
                limit=100,
            )
            self.assertEqual(len(result.entities), 50)
            self.assertGreaterEqual(len(result.relations), 49)
            self.assertTrue(all(
                item.project_id == "project-a" for item in result.entities
            ))

        measurements = {
            "path": await self._measure(path_query),
            "neighbors": await self._measure(neighbor_query),
            "subgraph_50_nodes": await self._measure(subgraph_query),
        }
        for name, result in measurements.items():
            self.assertLessEqual(
                result["p95_ms"], 500,
                f"{name} p95 exceeded 500ms on 5k entities: {result}",
            )

        # A project-B node/edge is invisible to a project-A caller, including
        # when explicitly supplied as a path endpoint or subgraph member.
        self.assertIsNone(await self.repository.get_entity(
            "secret-x", allowed_project_ids=self.allowed,
        ))
        self.assertIsNone(await self.service.shortest_path(
            "secret-x", "secret-y", allowed_project_ids=self.allowed,
        ))
        self.assertEqual((await self.service.neighbors(
            "secret-x", allowed_project_ids=self.allowed,
        )).entities, ())
        self.assertEqual((await self.service.match_subgraph(
            ("n00000", "secret-x"), allowed_project_ids=self.allowed,
        )).entities, ())

        other_repository = PostgresGraphRepository(
            tenant_id=self.other_tenant_id,
            connection_factory=self.repository._connect,
        )
        self.assertIsNone(await other_repository.get_entity(
            "n00000", allowed_project_ids=("project-a", "project-b"),
        ))

        # Include reproducible latency evidence in the CI test log.
        print("GRAPH_QUERY_BENCHMARK_5K=" + repr(measurements))


if __name__ == "__main__":
    unittest.main()
