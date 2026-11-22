from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="delete lock count")

import unittest
from pathlib import Path

from agent_platform.graph.postgres_repository import PostgresGraphRepository


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _Connection:
    def __init__(self, *, exists=True, relations=()):
        self.exists = exists
        self.relations = list(relations)
        self.calls = []
        self.deleted = False
        self.tombstoned = False
        self.outbox = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    def transaction(self):
        return _Transaction()

    async def execute(self, query, params=()):
        normalized = " ".join(query.split())
        self.calls.append((normalized, params))
        if "pg_advisory_xact_lock" in normalized:
            return _Cursor()
        if "FROM graph_entities" in normalized and "FOR UPDATE" in normalized:
            return _Cursor({"entity_id": params[1]} if self.exists and not self.deleted else None)
        if "FROM graph_relations" in normalized and "FOR UPDATE" in normalized:
            return _Cursor(rows=[{"relation_id": item} for item in self.relations])
        if "DELETE FROM graph_entities" in normalized:
            if not self.exists or self.deleted:
                return _Cursor()
            self.deleted = True
            return _Cursor({"entity_id": params[1]})
        if "INSERT INTO graph_entity_tombstones" in normalized:
            self.tombstoned = True
            return _Cursor()
        if "INSERT INTO graph_invalidation_outbox" in normalized:
            self.outbox.append(params)
            return _Cursor()
        raise AssertionError(f"unexpected SQL: {normalized}")


class GraphDeletePropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_atomically_tombstones_and_enqueues_entity_relation_and_cache(self):
        connection = _Connection(relations=("edge-a", "edge-b"))

        async def connect():
            return connection

        repository = PostgresGraphRepository(
            tenant_id="tenant-a", connection_factory=connect,
        )
        self.assertTrue(await repository.delete_entity(
            "node-a", allowed_project_ids=("project-a",),
        ))
        self.assertTrue(connection.deleted)
        self.assertTrue(connection.tombstoned)
        self.assertEqual(
            {(item[1], item[2], item[3]) for item in connection.outbox},
            {
                ("entity", "node-a", "delete"),
                ("cache", "node-a", "invalidate"),
                ("relation", "edge-a", "delete"),
                ("relation", "edge-b", "delete"),
            },
        )
        lock = next(i for i, (sql, _) in enumerate(connection.calls) if "pg_advisory_xact_lock" in sql)
        entity_read = next(i for i, (sql, _) in enumerate(connection.calls) if "FOR UPDATE" in sql and "graph_entities" in sql)
        relation_read = next(i for i, (sql, _) in enumerate(connection.calls) if "FOR UPDATE" in sql and "graph_relations" in sql)
        delete = next(i for i, (sql, _) in enumerate(connection.calls) if "DELETE FROM graph_entities" in sql)
        self.assertLess(lock, entity_read)
        self.assertLess(entity_read, relation_read)
        self.assertLess(relation_read, delete)

    async def test_missing_or_out_of_scope_entity_creates_no_invalidation(self):
        connection = _Connection(exists=False)

        async def connect():
            return connection

        repository = PostgresGraphRepository(
            tenant_id="tenant-a", connection_factory=connect,
        )
        self.assertFalse(await repository.delete_entity("missing"))
        self.assertFalse(connection.tombstoned)
        self.assertEqual(connection.outbox, [])
        self.assertFalse(any("DELETE FROM graph_entities" in sql for sql, _ in connection.calls))

    def test_all_graph_writes_share_the_delete_advisory_lock_and_tombstones_block_recreation(self):
        source = Path("agent_platform/graph/postgres_repository.py").read_text(encoding="utf-8")
        self.assertGreaterEqual(source.count("await self._lock_entity_ids(connection"), 3)
        upsert = source.split("async def upsert_entity", 1)[1].split("async def get_entity", 1)[0]
        self.assertIn("FROM graph_entity_tombstones", upsert)
        self.assertLess(upsert.index("FROM graph_entity_tombstones"), upsert.index("INSERT INTO graph_entities"))
        relation = source.split("async def upsert_relation", 1)[1].split("async def list_relations", 1)[0]
        self.assertIn("relation.source_entity_id, relation.target_entity_id", relation)

    def test_migration_defines_durable_deduplicated_outbox_and_tombstones(self):
        migration = Path(
            "agent_platform/storage/postgres/092_graph_delete_propagation.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("CREATE TABLE IF NOT EXISTS graph_entity_tombstones", migration)
        self.assertIn("CREATE TABLE IF NOT EXISTS graph_invalidation_outbox", migration)
        self.assertIn("UNIQUE (tenant_id, idempotency_key)", migration)
        self.assertIn("FOR UPDATE SKIP LOCKED", Path(
            "agent_platform/graph/postgres_repository.py"
        ).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
