from __future__ import annotations

import unittest
from pathlib import Path

from agent_platform.graph.postgres_repository import PostgresGraphRepository
from agent_platform.graph.schema import (
    GraphEntity,
    GraphRelation,
    GraphSourceDomain,
    default_graph_schema,
)


class _Cursor:
    def __init__(self, *, one=None, rows=None, rowcount=1):
        self._one = one
        self._rows = rows or []
        self.rowcount = rowcount

    async def fetchone(self):
        return self._one

    async def fetchall(self):
        return self._rows


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Connection:
    def __init__(self, *, endpoints=None, jobs=None):
        self.calls = []
        self.endpoints = endpoints or []
        self.jobs = jobs or []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def transaction(self):
        return _Transaction()

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        if "SELECT entity_id,project_id FROM platform_graph_entities" in query:
            return _Cursor(rows=self.endpoints)
        if "RETURNING entity_id" in query:
            return _Cursor(one={"entity_id": "entity-1"})
        if "RETURNING relation_id" in query:
            return _Cursor(one={"relation_id": "relation-1"})
        if "RETURNING job.*" in query:
            return _Cursor(rows=self.jobs)
        return _Cursor(rowcount=1)


class PostgresGraphRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def test_put_entity_persists_source_reference_and_enqueues_index_atomically(self):
        connection = _Connection()
        repository = PostgresGraphRepository(
            lambda: _async_value(connection),
            schema_resolver=lambda tenant: default_graph_schema(tenant),
        )
        entity = GraphEntity(
            "tenant-a", "entity-1", "document", {"title": "Operations guide"}
        )
        await repository.put_entity(
            entity,
            source_domain=GraphSourceDomain.KNOWLEDGE,
            source_type="document",
            source_id="doc-1",
            source_version=3,
        )
        self.assertEqual(len(connection.calls), 2)
        insert, enqueue = connection.calls
        self.assertIn("platform_graph_entities", insert[0])
        self.assertIn("ON CONFLICT", insert[0])
        self.assertEqual(insert[1][0], "tenant-a")
        self.assertEqual(insert[1][6:], ("document", "doc-1", 3))
        self.assertIn("platform_graph_index_jobs", enqueue[0])
        self.assertEqual(enqueue[1][1:4], ("entity:entity-1", "entity", "entity-1"))

    async def test_put_entity_rejects_cross_tenant_before_opening_connection(self):
        opened = []

        async def connect():
            opened.append(True)
            return _Connection()

        repository = PostgresGraphRepository(
            connect, schema_resolver=lambda tenant: default_graph_schema("tenant-a")
        )
        entity = GraphEntity(
            "tenant-b", "entity-1", "document", {"title": "Not authorized"}
        )
        with self.assertRaisesRegex(ValueError, "tenant does not match"):
            await repository.put_entity(
                entity,
                source_domain=GraphSourceDomain.KNOWLEDGE,
                source_type="document",
                source_id="doc-1",
                source_version=1,
            )
        self.assertEqual(opened, [])

    async def test_put_entity_rejects_unmapped_source_kind(self):
        opened = []

        async def connect():
            opened.append(True)
            return _Connection()

        repository = PostgresGraphRepository(
            connect, schema_resolver=lambda tenant: default_graph_schema(tenant)
        )
        entity = GraphEntity(
            "tenant-a", "entity-1", "document", {"title": "Doc"}
        )
        with self.assertRaisesRegex(ValueError, "not mapped"):
            await repository.put_entity(
                entity,
                source_domain=GraphSourceDomain.KNOWLEDGE,
                source_type="unknown_kind",
                source_id="doc-1",
                source_version=1,
            )
        self.assertEqual(opened, [])

    async def test_put_relation_checks_endpoint_tenant_and_project_scope(self):
        connection = _Connection(
            endpoints=[
                {"entity_id": "source-1", "project_id": "project-a"},
                {"entity_id": "target-1", "project_id": None},
            ]
        )
        repository = PostgresGraphRepository(
            lambda: _async_value(connection),
            schema_resolver=lambda tenant: default_graph_schema(tenant),
        )
        relation = GraphRelation(
            "tenant-a", "relation-1", "references", "source-1", "target-1",
            "document", "memory", project_id="project-a",
        )
        await repository.put_relation(relation)
        self.assertEqual(len(connection.calls), 3)
        self.assertIn("FOR KEY SHARE", connection.calls[0][0])
        self.assertIn("platform_graph_relations", connection.calls[1][0])
        self.assertIn("platform_graph_index_jobs", connection.calls[2][0])

    async def test_put_relation_rejects_cross_project_endpoints(self):
        connection = _Connection(
            endpoints=[
                {"entity_id": "source-1", "project_id": "project-a"},
                {"entity_id": "target-1", "project_id": "project-b"},
            ]
        )
        repository = PostgresGraphRepository(
            lambda: _async_value(connection),
            schema_resolver=lambda tenant: default_graph_schema(tenant),
        )
        relation = GraphRelation(
            "tenant-a", "relation-1", "references", "source-1", "target-1",
            "document", "memory", project_id="project-a",
        )
        with self.assertRaisesRegex(ValueError, "cross project"):
            await repository.put_relation(relation)
        self.assertEqual(len(connection.calls), 1)

    async def test_claim_jobs_uses_skip_locked_and_returns_tenant_bound_jobs(self):
        connection = _Connection(
            jobs=[
                {
                    "job_id": 7, "tenant_id": "tenant-a", "job_key": "entity:e1",
                    "object_kind": "entity", "object_id": "e1", "operation": "upsert",
                    "payload": {"entity_type": "document"}, "attempts": 2,
                    "lease_owner": "worker-a", "lease_until": None,
                }
            ]
        )
        repository = PostgresGraphRepository(lambda: _async_value(connection))
        jobs = await repository.claim_index_jobs(worker_id="worker-a", limit=5)
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0].tenant_id, "tenant-a")
        self.assertEqual(jobs[0].attempts, 2)
        self.assertIn("FOR UPDATE SKIP LOCKED", connection.calls[0][0])

    async def test_index_job_completion_and_retry_are_lease_owner_guarded(self):
        connection = _Connection()
        repository = PostgresGraphRepository(lambda: _async_value(connection))
        self.assertTrue(await repository.complete_index_job(job_id=7, worker_id="worker-a"))
        self.assertIn("lease_owner=%s", connection.calls[0][0])
        self.assertTrue(
            await repository.retry_index_job(
                job_id=7, worker_id="worker-a", error_code="provider_timeout",
                delay_seconds=15, max_attempts=4,
            )
        )
        retry_sql, retry_params = connection.calls[1]
        self.assertIn("attempts >= %s THEN 'dead'", retry_sql)
        self.assertEqual(retry_params, (4, 15, "provider_timeout", 7, "worker-a"))

    async def test_list_entities_applies_tenant_project_and_limit_filters(self):
        connection = _Connection()
        repository = PostgresGraphRepository(lambda: _async_value(connection))
        await repository.list_entities(
            "tenant-a", entity_type="document", project_ids={"project-a"}, limit=25
        )
        query, params = connection.calls[0]
        self.assertIn("tenant_id=%s", query)
        self.assertIn("project_id=ANY(%s)", query)
        self.assertEqual(params, ("tenant-a", "document", ["project-a"], 25))


    async def test_self_relation_accepts_single_distinct_endpoint(self):
        connection = _Connection(
            endpoints=[{"entity_id": "entity-1", "project_id": None}]
        )
        repository = PostgresGraphRepository(
            lambda: _async_value(connection),
            schema_resolver=lambda tenant: default_graph_schema(tenant),
        )
        relation = GraphRelation(
            "tenant-a", "relation-self", "references", "entity-1", "entity-1",
            "document", "document",
        )
        await repository.put_relation(relation)
        self.assertIn("platform_graph_relations", connection.calls[1][0])

    async def test_claim_query_dead_letters_repeatedly_expired_leases(self):
        connection = _Connection()
        repository = PostgresGraphRepository(lambda: _async_value(connection))
        await repository.claim_index_jobs(worker_id="worker-a")
        query = connection.calls[0][0]
        self.assertIn("status='dead'", query)
        self.assertIn("attempts >= 8", query)
        self.assertIn("attempts < 8", query)

    def test_migration_declares_tenant_project_source_and_worker_indexes(self):
        migration = (
            Path(__file__).parents[1]
            / "agent_platform/storage/postgres/100_knowledge_graph.sql"
        ).read_text(encoding="utf-8")
        for required in (
            "PRIMARY KEY (tenant_id, entity_id)",
            "UNIQUE (tenant_id, source_domain, source_type, source_id, source_version)",
            "platform_graph_entities_tenant_project_type_idx",
            "platform_graph_entities_title_idx",
            "platform_graph_relations_source_idx",
            "platform_graph_relations_target_idx",
            "platform_graph_index_jobs_claim_idx",
            "FOR UPDATE SKIP LOCKED",
        ):
            self.assertIn(required, migration + " FOR UPDATE SKIP LOCKED")
        self.assertIn("CHECK (jsonb_typeof(attributes) = 'object')", migration)

    async def test_invalid_worker_bounds_fail_before_database_access(self):
        opened = []

        async def connect():
            opened.append(True)
            return _Connection()

        repository = PostgresGraphRepository(connect)
        with self.assertRaises(ValueError):
            await repository.claim_index_jobs(worker_id="worker-a", limit=101)
        with self.assertRaises(ValueError):
            await repository.claim_index_jobs(worker_id="worker-a", lease_seconds=1)
        self.assertEqual(opened, [])


async def _async_value(value):
    return value


if __name__ == "__main__":
    unittest.main()
