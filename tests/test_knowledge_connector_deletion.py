from __future__ import annotations

import unittest

from agent_platform.knowledge.connectors import ConnectorPage
from agent_platform.knowledge.contracts import Document, KnowledgePrincipal
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class DeletionConnector:
    connector_id = "drive"

    def __init__(self, page):
        self.page = page
        self.calls = []

    async def list_changed(self, *, tenant_id, cursor, changed_since=None, limit=100):
        self.calls.append(cursor)
        return self.page

    async def revoke(self, *, tenant_id, credential_ref):
        return None


class ConnectorDeletionPropagationTests(unittest.IsolatedAsyncioTestCase):
    async def test_source_deletion_removes_chunks_from_retrieval_before_cursor_advances(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        document = Document(
            tenant_id="tenant-a", document_id="doc-a", title="Source",
            source_uri="drive://doc-a", content_hash="pending",
        )
        await service.ingest(document=document, content=b"unique source evidence")
        self.assertTrue(repository.chunks)

        connector = DeletionConnector(ConnectorPage(
            items=(), next_cursor="cursor-2", has_more=False,
            deleted_document_ids=("doc-a", "doc-a"),
        ))
        result = await service.sync_connector(connector=connector, tenant_id="tenant-a")

        self.assertEqual(result["deleted_documents"], 1)
        self.assertEqual(await repository.get_document("tenant-a", "doc-a"), None)
        self.assertEqual(repository.chunks, {})
        hits = await service.retrieve(
            principal=KnowledgePrincipal(tenant_id="tenant-a", user_id="user-a"),
            query="unique source evidence",
        )
        self.assertEqual(hits, [])
        self.assertEqual(await repository.get_connector_cursor("tenant-a", "drive"), "cursor-2")

    async def test_failed_deletion_does_not_advance_connector_cursor(self):
        class FailingRepository(InMemoryKnowledgeRepository):
            async def delete_document(self, tenant_id, document_id):
                raise RuntimeError("index cleanup unavailable")

        repository = FailingRepository()
        service = KnowledgeService(repository)
        connector = DeletionConnector(ConnectorPage(
            items=(), next_cursor="cursor-2", has_more=False,
            deleted_document_ids=("doc-a",),
        ))
        with self.assertRaisesRegex(RuntimeError, "cleanup unavailable"):
            await service.sync_connector(connector=connector, tenant_id="tenant-a")
        self.assertIsNone(await repository.get_connector_cursor("tenant-a", "drive"))

    async def test_connector_deletion_ids_must_be_non_empty_strings(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        connector = DeletionConnector(ConnectorPage(
            items=(), next_cursor="cursor-2", has_more=False,
            deleted_document_ids=("",),
        ))
        with self.assertRaisesRegex(ValueError, "non-empty IDs"):
            await service.sync_connector(connector=connector, tenant_id="tenant-a")
        self.assertIsNone(await repository.get_connector_cursor("tenant-a", "drive"))


if __name__ == "__main__":
    unittest.main()
