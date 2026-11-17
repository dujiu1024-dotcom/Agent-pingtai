from __future__ import annotations

import unittest

from agent_platform.knowledge.catalog import (
    InMemoryKnowledgeCatalog,
    IndexStatus,
    KnowledgeCollection,
    KnowledgeIndex,
)


class KnowledgeCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def test_collection_project_scope_and_tenant_isolation(self):
        catalog = InMemoryKnowledgeCatalog()
        shared = KnowledgeCollection(tenant_id="tenant-a", name="Shared")
        project = KnowledgeCollection(
            tenant_id="tenant-a", name="Project", project_id="project-a",
        )
        await catalog.create_collection(shared)
        await catalog.create_collection(project)
        self.assertEqual(
            {item.name for item in await catalog.list_collections(
                "tenant-a", frozenset(),
            )},
            {"Shared"},
        )
        self.assertIsNone(await catalog.get_collection(
            "tenant-b", shared.collection_id,
        ))
        self.assertEqual(
            {item.name for item in await catalog.list_collections(
                "tenant-a", frozenset({"project-a"}),
            )},
            {"Shared", "Project"},
        )

    async def test_membership_is_idempotent_and_tenant_scoped(self):
        catalog = InMemoryKnowledgeCatalog()
        collection = KnowledgeCollection(tenant_id="tenant-a", name="Docs")
        await catalog.create_collection(collection)
        self.assertTrue(await catalog.attach_document(
            "tenant-a", collection.collection_id, "doc-a",
        ))
        self.assertFalse(await catalog.attach_document(
            "tenant-a", collection.collection_id, "doc-a",
        ))
        self.assertNotIn(
            ("tenant-b", collection.collection_id, "doc-a"), catalog.members,
        )
        with self.assertRaises(ValueError):
            await catalog.attach_document("tenant-b", collection.collection_id, "doc-a")

    async def test_index_requires_collection_and_valid_dimensions(self):
        catalog = InMemoryKnowledgeCatalog()
        collection = KnowledgeCollection(tenant_id="tenant-a", name="Docs")
        await catalog.create_collection(collection)
        index = KnowledgeIndex(
            tenant_id="tenant-a", collection_id=collection.collection_id,
            index_name="semantic-v1", embedding_model="model-v1",
            dimensions=1536, status=IndexStatus.PENDING,
        )
        self.assertTrue(await catalog.create_index(index))
        self.assertFalse(await catalog.create_index(index))
        with self.assertRaises(ValueError):
            KnowledgeIndex(
                tenant_id="tenant-a", collection_id=collection.collection_id,
                index_name="bad", embedding_model="model-v1", dimensions=0,
            )

    async def test_collection_limit_is_bounded(self):
        catalog = InMemoryKnowledgeCatalog()
        with self.assertRaises(ValueError):
            await catalog.list_collections("tenant-a", frozenset(), limit=501)


if __name__ == "__main__":
    unittest.main()
