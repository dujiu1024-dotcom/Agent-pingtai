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
        service=KnowledgeService(InMemoryKnowledgeRepository()); doc=Document(tenant_id="t",title="pdf",source_uri="x",content_hash="x",media_type="image/png")
        with self.assertRaises(ValueError): await service.ingest(document=doc,content=b"\\x89PNG")
    async def test_chunking_preserves_source_offsets(self):
        from agent_platform.knowledge.chunking import chunk_document
        doc=Document(tenant_id="t",title="d",source_uri="x",content_hash="x"); chunks=chunk_document(doc,"a"*500,max_chars=200,overlap=20)
        self.assertGreater(len(chunks),1); self.assertEqual(chunks[0].start_offset,0); self.assertLess(chunks[1].start_offset,chunks[0].end_offset)

    async def test_query_rewrite_and_citation_provenance(self):
        class Rewriter:
            async def rewrite(self, query):
                return "normalized term"
        service=KnowledgeService(InMemoryKnowledgeRepository(),query_rewriter=Rewriter())
        doc=Document(tenant_id="t",title="source",source_uri="file://source.md",content_hash="x")
        await service.ingest(document=doc,content=b"normalized term appears here")
        hits=await service.retrieve(principal=KnowledgePrincipal(tenant_id="t",user_id="u"),query="vague question")
        self.assertEqual(hits[0].title,"source")
        from agent_platform.knowledge.citations import citation_context
        citation=citation_context(hits)[0]
        self.assertEqual(citation["document_version"],1)
        self.assertEqual(citation["source_uri"],"file://source.md")

    async def test_connector_ingestion_and_rag_evaluation(self):
        from agent_platform.knowledge.connectors import ConnectorDocument,ConnectorPage
        from agent_platform.knowledge.evaluation import RAGEvalCase,evaluate_retrieval
        class Connector:
            connector_id="fixture"
            async def list_changed(self,*,tenant_id,cursor,changed_since=None,limit=100):
                doc=Document(tenant_id=tenant_id,title="connector source",source_uri="connector://item/1",content_hash="x")
                return ConnectorPage(items=(ConnectorDocument(document=doc,content=b"evidence for retrieval",cursor="next"),),next_cursor="next",has_more=False)
            async def revoke(self,*,tenant_id,credential_ref):
                return None
        service=KnowledgeService(InMemoryKnowledgeRepository())
        results,cursor,more=await service.ingest_connector_page(connector=Connector(),tenant_id="t")
        self.assertEqual(len(results),1)
        self.assertEqual(cursor,"next")
        self.assertFalse(more)
        case=RAGEvalCase(case_id="case-1",query="evidence",expected_document_ids=frozenset({results[0].document.document_id}),principal=KnowledgePrincipal(tenant_id="t",user_id="u"))
        report=await evaluate_retrieval(service,[case],k=3)
        self.assertEqual(report[0].recall_at_k,1.0)
        self.assertEqual(report[0].citation_coverage,1.0)
