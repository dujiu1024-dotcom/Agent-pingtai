from __future__ import annotations

import unittest

from agent_platform.knowledge.connectors import ConnectorDocument, ConnectorPage
from agent_platform.knowledge.contracts import Document, KnowledgePrincipal
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class ConnectorDeletionSyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_incremental_tombstone_removes_document_and_is_reported(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        document = Document(
            tenant_id="tenant-a",
            title="source document",
            source_uri="connector://source/1",
            content_hash="pending",
        )

        class Connector:
            connector_id = "source-a"

            async def list_changed(self, *, tenant_id, cursor, changed_since=None, limit=100):
                if cursor is None:
                    return ConnectorPage(
                        items=(ConnectorDocument(document=document, content=b"searchable evidence"),),
                        next_cursor="cursor-1",
                        has_more=False,
                    )
                return ConnectorPage(
                    items=(),
                    next_cursor="cursor-2",
                    has_more=False,
                    deleted_document_ids=(document.document_id,),
                )

            async def revoke(self, *, tenant_id, credential_ref):
                return None

        connector = Connector()
        first = await service.sync_connector(connector=connector, tenant_id="tenant-a")
        self.assertEqual(first["documents"], 1)
        principal = KnowledgePrincipal(tenant_id="tenant-a", user_id="user-a")
        self.assertEqual(len(await service.retrieve(principal=principal, query="searchable")), 1)

        second = await service.sync_connector(connector=connector, tenant_id="tenant-a")
        self.assertEqual(second["deleted_documents"], 1)
        self.assertEqual(
            await service.retrieve(principal=principal, query="searchable"), []
        )
        self.assertIsNone(
            await repository.get_document("tenant-a", document.document_id)
        )

    async def test_connector_cannot_delete_another_connectors_document(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        document = Document(
            tenant_id="tenant-a",
            title="owned by source-a",
            source_uri="connector://source-a/1",
            content_hash="pending",
            metadata={"_knowledge_connector_id": "source-a"},
        )
        await service.ingest(document=document, content=b"protected evidence")

        class RogueConnector:
            connector_id = "source-b"

            async def list_changed(self, *, tenant_id, cursor, changed_since=None, limit=100):
                return ConnectorPage(
                    items=(),
                    next_cursor="cursor-1",
                    has_more=False,
                    deleted_document_ids=(document.document_id,),
                )

            async def revoke(self, *, tenant_id, credential_ref):
                return None

        with self.assertRaises(PermissionError):
            await service.sync_connector(connector=RogueConnector(), tenant_id="tenant-a")
        self.assertIsNotNone(
            await repository.get_document("tenant-a", document.document_id)
        )

    async def test_replayed_deletion_tombstone_is_idempotent(self):
        service = KnowledgeService(InMemoryKnowledgeRepository())

        class Connector:
            connector_id = "source-a"

            async def list_changed(self, *, tenant_id, cursor, changed_since=None, limit=100):
                return ConnectorPage(
                    items=(),
                    next_cursor="cursor-1",
                    has_more=False,
                    deleted_document_ids=("missing-document",),
                )

            async def revoke(self, *, tenant_id, credential_ref):
                return None

        result = await service.sync_connector(connector=Connector(), tenant_id="tenant-a")
        self.assertEqual(result["deleted_documents"], 0)


if __name__ == "__main__":
    unittest.main()
