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
        result=await service.sync_connector(connector=Connector(),tenant_id="t")
        self.assertEqual(result["documents"],1)
        self.assertEqual(result["cursor"],"next")
        self.assertFalse(result["has_more"])
        self.assertEqual(await service.repository.get_connector_cursor("t","fixture"),"next")
        case=RAGEvalCase(case_id="case-1",query="evidence",expected_document_ids=frozenset({next(iter(service.repository.documents.values())).document_id}),principal=KnowledgePrincipal(tenant_id="t",user_id="u"))
        report=await evaluate_retrieval(service,[case],k=3)
        self.assertEqual(report[0].recall_at_k,1.0)
        self.assertEqual(report[0].citation_coverage,1.0)

    async def test_list_documents_respects_scope_and_latest_version(self):
        from dataclasses import replace
        from agent_platform.knowledge.contracts import KnowledgeScope
        service=KnowledgeService(InMemoryKnowledgeRepository())
        tenant=Document(tenant_id="t",title="tenant v1",source_uri="file://tenant",content_hash="x")
        await service.ingest(document=tenant,content=b"first version")
        newer=replace(tenant,version=2,title="tenant v2")
        await service.ingest(document=newer,content=b"second version")
        private=Document(tenant_id="t",title="private",source_uri="file://private",content_hash="x",scope=KnowledgeScope.PRIVATE,owner_user_id="u2")
        await service.ingest(document=private,content=b"private content")
        changing_scope=Document(tenant_id="t",title="shared v1",source_uri="file://changing-scope",content_hash="x")
        await service.ingest(document=changing_scope,content=b"formerly shared")
        private_revision=replace(changing_scope,version=2,title="private revision",scope=KnowledgeScope.PRIVATE,owner_user_id="u2")
        await service.ingest(document=private_revision,content=b"now private")
        docs=await service.list_documents(principal=KnowledgePrincipal(tenant_id="t",user_id="u1"))
        self.assertEqual({doc.title for doc in docs},{"tenant v2"})
        owner_docs=await service.list_documents(principal=KnowledgePrincipal(tenant_id="t",user_id="u2"))
        self.assertEqual({doc.title for doc in owner_docs},{"tenant v2","private","private revision"})

    async def test_chunking_recovers_pdf_page_number(self):
        from agent_platform.knowledge.chunking import chunk_document
        doc=Document(tenant_id="t",title="pdf",source_uri="file://pdf",content_hash="x")
        text="[page:1] " + "a"*110 + "\n[page:2] second page text"
        chunks=chunk_document(doc,text,max_chars=100,overlap=0)
        self.assertEqual(chunks[-1].page_number,2)

    async def test_search_excludes_superseded_document_versions(self):
        from dataclasses import replace
        service=KnowledgeService(InMemoryKnowledgeRepository())
        original=Document(tenant_id="t",title="policy",source_uri="file://policy",content_hash="x")
        await service.ingest(document=original,content=b"obsolete policy phrase")
        revised=replace(original,version=2)
        await service.ingest(document=revised,content=b"current policy phrase")
        principal=KnowledgePrincipal(tenant_id="t",user_id="u")
        self.assertEqual(await service.retrieve(principal=principal,query="obsolete"),[])
        hits=await service.retrieve(principal=principal,query="current")
        self.assertEqual(len(hits),1)
        self.assertEqual(hits[0].document_version,2)

    async def test_html_parser_extracts_text_without_script_and_with_newlines(self):
        service=KnowledgeService(InMemoryKnowledgeRepository())
        doc=Document(tenant_id="t",title="html",source_uri="file://html",content_hash="x",media_type="text/html")
        await service.ingest(document=doc,content=b"<p>first</p><script>secret()</script><p>second</p>")
        hits=await service.retrieve(principal=KnowledgePrincipal(tenant_id="t",user_id="u"),query="second")
        self.assertIn("\n",hits[0].text)
        self.assertNotIn("secret",hits[0].text)

    async def test_ingestion_auto_versions_updates_and_retries_idempotently(self):
        service=KnowledgeService(InMemoryKnowledgeRepository())
        doc=Document(tenant_id="t",title="connector doc",source_uri="connector://doc/1",content_hash="source-hash")
        first=await service.ingest(document=doc,content=b"initial content")
        retry=await service.ingest(document=doc,content=b"initial content")
        self.assertEqual(first.document.version,1)
        self.assertEqual(retry.document.version,1)
        self.assertTrue(retry.skipped_unchanged)
        self.assertEqual(first.chunks[0].chunk_id,retry.chunks[0].chunk_id)
        changed=await service.ingest(document=doc,content=b"updated content")
        self.assertEqual(changed.document.version,2)
        changed_retry=await service.ingest(document=doc,content=b"updated content")
        self.assertEqual(changed_retry.document.version,2)
        self.assertEqual(changed.chunks[0].chunk_id,changed_retry.chunks[0].chunk_id)

    async def test_rag_answer_requires_valid_retrieved_citations(self):
        from agent_platform.knowledge.rag import RAGService
        knowledge=KnowledgeService(InMemoryKnowledgeRepository())
        doc=Document(tenant_id="t",title="source",source_uri="file://source",content_hash="x")
        await knowledge.ingest(document=doc,content=b"verified evidence")
        class GroundedGenerator:
            async def generate(self,*,query,context):
                return {"answer":"The evidence supports this.", "citation_ids":[context[0]["citation_id"]]}
        principal=KnowledgePrincipal(tenant_id="t",user_id="u")
        grounded=await RAGService(knowledge=knowledge,generator=GroundedGenerator()).answer(principal=principal,query="verified")
        self.assertFalse(grounded.abstained)
        self.assertEqual(len(grounded.citations),1)
        class HallucinatedCitationGenerator:
            async def generate(self,*,query,context):
                return {"answer":"Unsupported claim.", "citation_ids":["not-in-context"]}
        rejected=await RAGService(knowledge=knowledge,generator=HallucinatedCitationGenerator()).answer(principal=principal,query="verified")
        self.assertTrue(rejected.abstained)
        self.assertEqual(rejected.reason,"unknown_citation")
        self.assertEqual(rejected.answer,"")
