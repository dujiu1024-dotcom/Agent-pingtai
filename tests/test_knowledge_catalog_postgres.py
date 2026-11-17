from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.knowledge.catalog import (
    IndexStatus,
    KnowledgeCollection,
    KnowledgeIndex,
    PostgresKnowledgeCatalog,
)


class FakeCursor:
    def __init__(self, *, one=None, many=None):
        self.one = one
        self.many = many or []

    async def fetchone(self):
        return self.one

    async def fetchall(self):
        return self.many


class FakeTransaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class FakeConnection:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False

    def transaction(self):
        return FakeTransaction()

    async def execute(self, query, params=()):
        self.calls.append((query, params))
        result = self.results.pop(0) if self.results else {}
        return FakeCursor(one=result.get("one"), many=result.get("many"))


class PostgresKnowledgeCatalogTests(unittest.IsolatedAsyncioTestCase):
    def make_catalog(self, *results):
        connection = FakeConnection(results)

        async def connect():
            return connection

        return PostgresKnowledgeCatalog(connect), connection

    async def test_create_collection_persists_creator_and_tenant_scope(self):
        catalog, connection = self.make_catalog({"one": {"collection_id": "collection-a"}})
        collection = KnowledgeCollection(
            tenant_id="tenant-a", name="Research", created_by="user-a",
        )
        self.assertTrue(await catalog.create_collection(collection))
        query, params = connection.calls[0]
        self.assertIn("INSERT INTO knowledge_collections", query)
        self.assertIn("created_by", query)
        self.assertEqual(params[0], "tenant-a")
        self.assertEqual(params[-1], "user-a")

    async def test_list_collections_is_tenant_and_project_scoped(self):
        now = datetime.now(timezone.utc)
        catalog, connection = self.make_catalog({"many": [{
            "tenant_id": "tenant-a", "collection_id": "collection-a",
            "name": "Project", "description": "", "project_id": "project-a",
            "created_by": "user-a", "created_at": now, "updated_at": now,
            "deleted_at": None,
        }]})
        rows = await catalog.list_collections(
            "tenant-a", frozenset({"project-a"}), limit=20,
        )
        self.assertEqual([row.collection_id for row in rows], ["collection-a"])
        query, params = connection.calls[0]
        self.assertIn("tenant_id = %s", query)
        self.assertIn("project_id = ANY(%s)", query)
        self.assertEqual(params, ("tenant-a", ["project-a"], 20))

    async def test_collection_membership_rejects_incompatible_or_missing_document(self):
        catalog, connection = self.make_catalog({}, {})
        with self.assertRaisesRegex(ValueError, "compatible document"):
            await catalog.attach_document("tenant-a", "collection-a", "private-doc")
        query, params = connection.calls[0]
        self.assertIn("d.scope = 'tenant'", query)
        self.assertIn("d.scope = 'project'", query)
        self.assertIn("d.project_id = c.project_id", query)
        self.assertEqual(params, ("private-doc", "tenant-a", "collection-a"))

    async def test_index_creation_uses_live_tenant_collection_and_unique_name(self):
        catalog, connection = self.make_catalog({"one": {"index_id": "index-a"}})
        index = KnowledgeIndex(
            tenant_id="tenant-a", collection_id="collection-a",
            index_name="semantic-v1", embedding_model="embed-v1",
            dimensions=1536, status=IndexStatus.PENDING,
        )
        self.assertTrue(await catalog.create_index(index))
        query, params = connection.calls[0]
        self.assertIn("FROM knowledge_collections c", query)
        self.assertIn("c.tenant_id = %s", query)
        self.assertIn("ON CONFLICT (tenant_id, collection_id, index_name)", query)
        self.assertEqual(params[-2:], ("tenant-a", "collection-a"))


if __name__ == "__main__":
    unittest.main()
