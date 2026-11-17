from __future__ import annotations

import unittest

from agent_platform.knowledge.citations import citation_context
from agent_platform.knowledge.contracts import Document, KnowledgePrincipal
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class KnowledgeAclPropagationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryKnowledgeRepository()
        self.service = KnowledgeService(self.repository)
        self.manager = KnowledgePrincipal(
            tenant_id="tenant-a", user_id="owner",
            permissions=frozenset({"knowledge.manage"}),
        )
        self.reader = KnowledgePrincipal(tenant_id="tenant-a", user_id="reader")
        self.document = Document(
            tenant_id="tenant-a", title="ACL source",
            source_uri="s3://bucket/acl-source.pdf", content_hash="pending",
        )
        self.ingested = await self.service.ingest(
            document=self.document,
            content=b"alpha confidential evidence. beta public evidence.",
            max_chars=100,
            overlap=0,
        )
        self.chunk = self.ingested.chunks[0]

    async def test_chunk_acl_filters_search_and_preview_before_returning_text(self):
        await self.service.grant_chunk_access(
            principal=self.manager, document_id=self.document.document_id,
            chunk_id=self.chunk.chunk_id, principal_type="user",
            principal_id="reader", permission="read",
        )
        # A chunk ACL is deny-by-default for that fragment, not a document grant.
        self.assertEqual(
            await self.service.retrieve(principal=self.manager, query="confidential"), []
        )
        self.assertEqual(
            await self.service.list_document_chunks(
                principal=self.manager, document_id=self.document.document_id,
            ), [],
        )
        allowed = await self.service.retrieve(principal=self.reader, query="confidential")
        self.assertEqual([hit.chunk_id for hit in allowed], [self.chunk.chunk_id])
        self.assertEqual(
            [item.chunk_id for item in await self.service.list_document_chunks(
                principal=self.reader, document_id=self.document.document_id,
            )],
            [self.chunk.chunk_id],
        )

    async def test_acl_revocation_invalidates_cache_and_stays_fail_closed(self):
        await self.service.grant_chunk_access(
            principal=self.manager, document_id=self.document.document_id,
            chunk_id=self.chunk.chunk_id, principal_type="user",
            principal_id="reader", permission="read",
        )
        self.assertEqual(len(await self.service.retrieve(
            principal=self.reader, query="confidential",
        )), 1)
        self.assertTrue(await self.service.revoke_chunk_access(
            principal=self.manager, document_id=self.document.document_id,
            chunk_id=self.chunk.chunk_id, principal_type="user",
            principal_id="reader", permission="read",
        ))
        self.assertEqual(await self.service.retrieve(
            principal=self.reader, query="confidential",
        ), [])
        self.assertEqual(
            self.repository.cache_invalidations[-1]["reason"], "acl_changed",
        )
        self.assertFalse(await self.service.revoke_chunk_access(
            principal=self.manager, document_id=self.document.document_id,
            chunk_id=self.chunk.chunk_id, principal_type="user",
            principal_id="reader", permission="read",
        ))

    async def test_document_acl_revoke_and_replace_emit_invalidation(self):
        await self.service.grant_document_access(
            principal=self.manager, document_id=self.document.document_id,
            principal_type="user", principal_id="reader",
        )
        self.assertTrue(await self.service.revoke_document_access(
            principal=self.manager, document_id=self.document.document_id,
            principal_type="user", principal_id="reader",
        ))
        self.assertEqual(self.repository.cache_invalidations[-1]["reason"], "acl_changed")
        await self.repository.replace_document_access(
            "tenant-a", self.document.document_id, [], "connector-sync",
        )
        self.assertEqual(self.repository.cache_invalidations[-1]["resource_type"], "document")

    async def test_delete_removes_acl_state_and_emits_document_and_chunk_tombstones(self):
        await self.service.grant_chunk_access(
            principal=self.manager, document_id=self.document.document_id,
            chunk_id=self.chunk.chunk_id, principal_type="user",
            principal_id="reader", permission="read",
        )
        self.assertTrue(await self.service.delete_document(
            principal=self.manager, document_id=self.document.document_id,
        ))
        self.assertNotIn(("tenant-a", self.chunk.chunk_id), self.repository.chunk_acl)
        self.assertNotIn(("tenant-a", self.chunk.chunk_id), self.repository.chunk_acl_enforced)
        deleted = [item for item in self.repository.cache_invalidations if item["reason"] == "deleted"]
        self.assertEqual({item["resource_type"] for item in deleted}, {"document", "chunk"})
        self.assertEqual(await self.service.retrieve(
            principal=self.reader, query="confidential",
        ), [])

    async def test_citations_include_exact_document_version_chunk_and_locator(self):
        hit = (await self.service.retrieve(
            principal=self.manager, query="confidential",
        ))[0]
        citation = citation_context([hit])[0]
        self.assertEqual(citation["document_id"], self.document.document_id)
        self.assertEqual(citation["document_version"], 1)
        self.assertEqual(citation["chunk_id"], self.chunk.chunk_id)
        self.assertEqual(citation["source_uri"], self.document.source_uri)
        self.assertEqual(citation["source_locator"], {
            "page_number": self.chunk.page_number,
            "start_offset": self.chunk.start_offset,
            "end_offset": self.chunk.end_offset,
        })


if __name__ == "__main__":
    unittest.main()
