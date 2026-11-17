from __future__ import annotations

import unittest

from agent_platform.knowledge.chunking import CLEANING_VERSION, clean_document_text, chunk_document, sha256_text
from agent_platform.knowledge.contracts import Document, KnowledgePrincipal
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class KnowledgeContentPipelineTests(unittest.IsolatedAsyncioTestCase):
    def test_cleaning_is_canonical_and_removes_control_noise(self):
        self.assertEqual(
            clean_document_text("  Cafe\u0301\talpha\x00\r\n\n\n beta  "),
            "Café alpha\n\nbeta",
        )
        self.assertEqual(clean_document_text(" \ufeff\x00 "), "")

    def test_chunks_have_deterministic_hashes_and_source_locators(self):
        document = Document(
            tenant_id="tenant-a", title="source", source_uri="s3://bucket/source.pdf",
            content_hash="pending",
        )
        text = "A" * 130 + "\n[page:2]\n" + "B" * 130
        first = chunk_document(document, text, max_chars=120, overlap=20)
        second = chunk_document(document, text, max_chars=120, overlap=20)
        self.assertEqual([item.chunk_id for item in first], [item.chunk_id for item in second])
        self.assertEqual([item.content_hash for item in first], [sha256_text(item.text) for item in first])
        self.assertEqual(first[0].metadata["cleaning_version"], CLEANING_VERSION)
        self.assertEqual(first[0].metadata["source_uri"], document.source_uri)
        self.assertEqual(first[0].metadata["source_locator"]["start_offset"], first[0].start_offset)
        self.assertTrue(any(item.page_number == 2 for item in first))

    async def test_ingest_skips_unchanged_content_and_creates_new_version_on_change(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        document = Document(
            tenant_id="tenant-a", title="incremental", source_uri="file://incremental",
            content_hash="pending",
        )
        first = await service.ingest(document=document, content=b"Alpha\x00 beta")
        repeated = await service.ingest(document=document, content=b"Alpha beta")
        self.assertFalse(first.skipped_unchanged)
        self.assertTrue(repeated.skipped_unchanged)
        self.assertEqual(repeated.document.version, 1)
        self.assertEqual([item.chunk_id for item in first.chunks], [item.chunk_id for item in repeated.chunks])

        updated = await service.ingest(document=document, content=b"Alpha beta revised")
        self.assertFalse(updated.skipped_unchanged)
        self.assertEqual(updated.document.version, 2)
        self.assertEqual(len(await repository.list_chunks("tenant-a", document.document_id, 1)), len(first.chunks))
        self.assertEqual(len(await repository.list_chunks("tenant-a", document.document_id, 2)), len(updated.chunks))

    async def test_delete_is_idempotent_and_removes_document_from_search(self):
        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        document = Document(
            tenant_id="tenant-a", title="delete me", source_uri="file://delete",
            content_hash="pending",
        )
        await service.ingest(document=document, content=b"deletion marker")
        principal = KnowledgePrincipal(
            tenant_id="tenant-a", user_id="owner",
            permissions=frozenset({"knowledge.manage"}),
        )
        self.assertEqual(len(await service.retrieve(principal=principal, query="deletion")), 1)
        self.assertTrue(await service.delete_document(principal=principal, document_id=document.document_id))
        self.assertFalse(await service.delete_document(principal=principal, document_id=document.document_id))
        self.assertEqual(await service.retrieve(principal=principal, query="deletion"), [])


if __name__ == "__main__":
    unittest.main()
