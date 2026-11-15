from __future__ import annotations
import unittest
from agent_platform.knowledge.contracts import Document,KnowledgePrincipal,KnowledgeScope
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
class KnowledgeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_ingest_and_acl_filter_before_return(self):
        service=KnowledgeService(InMemoryKnowledgeRepository())
        public=Document(tenant_id="t1",title="public",source_uri="s3://public",content_hash="pending")
        private=Document(tenant_id="t1",title="private",source_uri="s3://private",content_hash="pending",scope=KnowledgeScope.PRIVATE,owner_user_id="u2")
        await service.ingest(document=public,content=b"alpha beta source"); await service.ingest(document=private,content=b"alpha secret source")
        hits=await service.retrieve(principal=KnowledgePrincipal(tenant_id="t1",user_id="u1"),query="alpha",limit=10)
        self.assertEqual({h.title for h in hits},{"public"})
    async def test_tenant_isolation(self):
        service=KnowledgeService(InMemoryKnowledgeRepository())
        await service.ingest(document=Document(tenant_id="t2",title="secret",source_uri="x",content_hash="x"),content=b"confidential")
        self.assertEqual(await service.retrieve(principal=KnowledgePrincipal(tenant_id="t1",user_id="u1"),query="confidential"),[])
    async def test_parser_rejects_unknown_binary_type(self):
        service=KnowledgeService(InMemoryKnowledgeRepository()); doc=Document(tenant_id="t",title="pdf",source_uri="x",content_hash="x",media_type="application/pdf")
        with self.assertRaises(ValueError): await service.ingest(document=doc,content=b"%PDF")
    async def test_chunking_preserves_source_offsets(self):
        from agent_platform.knowledge.chunking import chunk_document
        doc=Document(tenant_id="t",title="d",source_uri="x",content_hash="x"); chunks=chunk_document(doc,"a"*500,max_chars=200,overlap=20)
        self.assertGreater(len(chunks),1); self.assertEqual(chunks[0].start_offset,0); self.assertLess(chunks[1].start_offset,chunks[0].end_offset)
