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

    async def test_collection_contract_rejects_wrong_types_and_oversized_names(self):
        invalid_values = (
            {"tenant_id": 7, "name": "Docs"},
            {"tenant_id": "tenant-a", "name": 42},
            {"tenant_id": "tenant-a", "name": "x" * 201},
            {"tenant_id": "tenant-a", "name": "Docs", "description": None},
            {"tenant_id": "tenant-a", "name": "Docs", "project_id": " "},
        )
        for values in invalid_values:
            with self.subTest(values=values), self.assertRaises(ValueError):
                KnowledgeCollection(**values)

    async def test_index_contract_rejects_bool_float_and_invalid_status(self):
        base = {
            "tenant_id": "tenant-a", "collection_id": "collection-a",
            "index_name": "semantic", "embedding_model": "model-v1",
            "dimensions": 1536,
        }
        invalid_values = (
            {"dimensions": True},
            {"dimensions": 1.5},
            {"schema_version": True},
            {"schema_version": 1.5},
            {"status": "ready"},
            {"index_id": ""},
        )
        for override in invalid_values:
            with self.subTest(override=override), self.assertRaises(ValueError):
                KnowledgeIndex(**(base | override))

    async def test_collection_limit_rejects_boolean_and_non_integer(self):
        catalog = InMemoryKnowledgeCatalog()
        for limit in (True, 1.5, "10"):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                await catalog.list_collections("tenant-a", frozenset(), limit=limit)

    async def test_collection_limit_is_bounded(self):
        catalog = InMemoryKnowledgeCatalog()
        with self.assertRaises(ValueError):
            await catalog.list_collections("tenant-a", frozenset(), limit=501)


if __name__ == "__main__":
    unittest.main()
