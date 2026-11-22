"""PostgreSQL integration coverage for tenant-scoped knowledge catalog."""
from __future__ import annotations

import os
import unittest
from pathlib import Path
from uuid import uuid4

DSN = os.environ.get("AGENT_PLATFORM_TEST_DSN")
if DSN:
    from psycopg import AsyncConnection
    from psycopg.rows import dict_row

    from agent_platform.knowledge.catalog import (
        IndexStatus,
        KnowledgeCollection,
        KnowledgeIndex,
        PostgresKnowledgeCatalog,
    )
    from agent_platform.knowledge.contracts import Document, KnowledgeScope
    from agent_platform.knowledge.postgres_repository import PostgresKnowledgeRepository


@unittest.skipUnless(DSN, "AGENT_PLATFORM_TEST_DSN is not configured")
class KnowledgeCatalogPostgresIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant_id = "knowledge-catalog-" + uuid4().hex
        async with await AsyncConnection.connect(DSN, autocommit=True) as conn:
            for path in sorted(Path("agent_platform/storage/postgres").glob("*.sql")):
                await conn.execute(path.read_text(), prepare=False)
        async with await AsyncConnection.connect(DSN) as conn:
            await conn.execute(
                "INSERT INTO platform_tenants(tenant_id, display_name) VALUES(%s, %s)",
                (self.tenant_id, "Knowledge Catalog IT"),
            )
            await conn.commit()

        async def connect():
            return await AsyncConnection.connect(DSN, row_factory=dict_row)

        self.catalog = PostgresKnowledgeCatalog(connect)
        self.knowledge = PostgresKnowledgeRepository(connect)

    async def test_collection_membership_index_and_scope_isolation(self):
        shared = KnowledgeCollection(
            tenant_id=self.tenant_id, name="Tenant documents", created_by="owner",
        )
        project = KnowledgeCollection(
            tenant_id=self.tenant_id, name="Project A documents",
            project_id="project-a", created_by="owner",
        )
        self.assertTrue(await self.catalog.create_collection(shared))
        self.assertTrue(await self.catalog.create_collection(project))
        self.assertFalse(await self.catalog.create_collection(shared))

        tenant_doc = Document(
            tenant_id=self.tenant_id, title="Tenant doc", source_uri="upload:tenant",
            content_hash="sha256:tenant", scope=KnowledgeScope.TENANT,
        )
        project_doc = Document(
            tenant_id=self.tenant_id, title="Project doc", source_uri="upload:project",
            content_hash="sha256:project", scope=KnowledgeScope.PROJECT,
            project_id="project-a",
        )
        other_project_doc = Document(
            tenant_id=self.tenant_id, title="Other project doc", source_uri="upload:other",
            content_hash="sha256:other", scope=KnowledgeScope.PROJECT,
            project_id="project-b",
        )
        private_doc = Document(
            tenant_id=self.tenant_id, title="Private doc", source_uri="upload:private",
            content_hash="sha256:private", scope=KnowledgeScope.PRIVATE,
            owner_user_id="owner",
        )
        for document in (tenant_doc, project_doc, other_project_doc, private_doc):
            self.assertTrue(await self.knowledge.save_document(document, []))

        self.assertTrue(await self.catalog.attach_document(
            self.tenant_id, shared.collection_id, tenant_doc.document_id,
        ))
        self.assertFalse(await self.catalog.attach_document(
            self.tenant_id, shared.collection_id, tenant_doc.document_id,
        ))
        self.assertTrue(await self.catalog.attach_document(
            self.tenant_id, project.collection_id, project_doc.document_id,
        ))
        for document in (project_doc, other_project_doc, private_doc):
            if document is project_doc:
                continue
            with self.assertRaises(ValueError):
                await self.catalog.attach_document(
                    self.tenant_id, project.collection_id, document.document_id,
                )

        index = KnowledgeIndex(
            tenant_id=self.tenant_id, collection_id=project.collection_id,
            index_name="semantic-v1", embedding_model="embed-v1",
            dimensions=1024, status=IndexStatus.PENDING,
        )
        self.assertTrue(await self.catalog.create_index(index))
        self.assertFalse(await self.catalog.create_index(index))
        indexes = await self.catalog.list_indexes(
            self.tenant_id, project.collection_id,
        )
        self.assertEqual([item.index_id for item in indexes], [index.index_id])

        self.assertIsNone(await self.catalog.get_collection(
            "another-tenant", shared.collection_id,
        ))
        visible = await self.catalog.list_collections(
            self.tenant_id, frozenset({"project-a"}), limit=20,
        )
        self.assertEqual(
            {item.collection_id for item in visible},
            {shared.collection_id, project.collection_id},
        )


if __name__ == "__main__":
    unittest.main()
