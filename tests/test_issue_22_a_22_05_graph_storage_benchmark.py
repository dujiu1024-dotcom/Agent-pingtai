from __future__ import annotations

import asyncio
import unittest

from scripts.benchmark_graph_storage import (
    percentile,
    run_benchmark,
    summarize_latencies,
)


class FakeGraphRepository:
    def __init__(self) -> None:
        self.entities = {}
        self.relations = {}
        self.active = 0
        self.max_active = 0

    async def _enter(self):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0)
        self.active -= 1

    async def upsert_entity(self, entity, *, name):
        await self._enter()
        self.entities[entity.entity_id] = (entity, name)
        return entity

    async def upsert_relation(self, relation):
        await self._enter()
        self.relations[relation.relation_id] = relation
        return relation

    async def search_entities(self, query, *, limit):
        await self._enter()
        return list(self.entities.values())[:limit]

    async def list_relations(self, entity_id, *, direction, limit):
        await self._enter()
        return list(self.relations.values())[:limit]


class GraphStorageBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    def test_percentile_and_empty_summary(self):
        self.assertEqual(percentile([], 0.95), 0.0)
        self.assertEqual(percentile([1.0, 2.0, 3.0, 4.0], 0.50), 3.0)
        summary = summarize_latencies([1.0, 2.0, 3.0, 4.0])
        self.assertEqual(summary.count, 4)
        self.assertEqual(summary.max_ms, 4.0)

    async def test_runs_concurrent_entity_relation_and_query_workloads(self):
        repository = FakeGraphRepository()
        result = await run_benchmark(
            repository,
            tenant_id="tenant-benchmark",
            prefix="bench_test_",
            workers=3,
            entities_per_worker=4,
            query_iterations=5,
        )
        self.assertEqual(len(repository.entities), 12)
        self.assertEqual(len(repository.relations), 12)
        self.assertEqual(result["workload"]["workers"], 3)
        self.assertEqual(result["writes"]["entity"]["latency"]["count"], 12)
        self.assertEqual(result["writes"]["relation"]["latency"]["count"], 12)
        self.assertEqual(result["queries"]["entity_prefix_search"]["count"], 5)
        self.assertEqual(result["queries"]["adjacency_lookup"]["count"], 5)
        self.assertGreaterEqual(result["writes"]["entity"]["throughput_per_second"], 0)
        self.assertGreaterEqual(repository.max_active, 2)
        self.assertLessEqual(repository.max_active, 3)

    async def test_rejects_non_positive_workload(self):
        with self.assertRaisesRegex(ValueError, "must be positive"):
            await run_benchmark(
                FakeGraphRepository(),
                tenant_id="tenant-benchmark",
                prefix="bench_test_",
                workers=0,
                entities_per_worker=1,
                query_iterations=1,
            )


if __name__ == "__main__":
    unittest.main()
