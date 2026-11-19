from __future__ import annotations

import threading
import unittest
from types import SimpleNamespace

from agent_platform.knowledge.contracts import Document, KnowledgePrincipal, KnowledgeScope
from agent_platform.knowledge.evaluation import RAGEvalCase, evaluate_retrieval
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class KnowledgeServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_retrieval_precision_counts_unique_documents_not_duplicate_chunks(self):
        class FixtureService:
            async def retrieve(self, *, principal, query, limit):
                return [
                    SimpleNamespace(document_id="doc-a", source_uri="source:a", document_version=1, chunk_id="a1"),
                    SimpleNamespace(document_id="doc-a", source_uri="source:a", document_version=1, chunk_id="a2"),
                    SimpleNamespace(document_id="doc-b", source_uri="source:b", document_version=1, chunk_id="b1"),
                ][:limit]

        from agent_platform.knowledge.evaluation import RAGEvalCase, evaluate_retrieval

        principal = KnowledgePrincipal(tenant_id="tenant", user_id="user")
        results = await evaluate_retrieval(
            FixtureService(),
            [RAGEvalCase("duplicate-chunks", "query", frozenset({"doc-a"}), principal)],
            k=3,
        )
        self.assertEqual(results[0].retrieved_document_ids, ("doc-a", "doc-b"))
        self.assertEqual(results[0].precision_at_k, 0.5)
        self.assertEqual(results[0].recall_at_k, 1.0)

    async def test_ingestion_rejects_invalid_upload_and_chunk_contracts(self):
        repository = InMemoryKnowledgeRepository()
        for value in (True, 1024.5, "2048", 0, 1023):
            with self.subTest(max_upload_bytes=value), self.assertRaises(ValueError):
                KnowledgeService(repository, max_upload_bytes=value)

        service = KnowledgeService(repository)
        document = Document(
            tenant_id="t", title="contract", source_uri="memory://contract",
            content_hash="pending",
        )
        for max_chars, overlap in ((0, 0), (True, 0), (10, -1), (10, True), (10, 10), (10, 1.5)):
            with self.subTest(max_chars=max_chars, overlap=overlap), self.assertRaises(ValueError):
                await service.ingest(document=document, content=b"valid bytes", max_chars=max_chars, overlap=overlap)
        with self.assertRaises(TypeError):
            await service.ingest(document=document, content="not bytes")

    async def test_ingest_and_acl_filter_before_return(self):
        service=KnowledgeService(InMemoryKnowledgeRepository())
        public=Document(tenant_id="t1",title="public",source_uri="s3://public",content_hash="pending")
        private=Document(tenant_id="t1",title="private",source_uri="s3://private",content_hash="pending",scope=KnowledgeScope.PRIVATE,owner_user_id="u2")
        await service.ingest(document=public,content=b"alpha beta source"); await service.ingest(document=private,content=b"alpha secret source")
        hits=await service.retrieve(principal=KnowledgePrincipal(tenant_id="t1",user_id="u1"),query="alpha",limit=10)
        self.assertEqual({h.title for h in hits},{"public"})
    async def test_document_acl_grant_and_revoke_filter_before_search(self):
        service = KnowledgeService(InMemoryKnowledgeRepository())
        document = Document(
            tenant_id="t1", title="restricted", source_uri="file://restricted",
            content_hash="pending",
        )
        await service.ingest(document=document, content=b"restricted evidence")
        owner = KnowledgePrincipal(
            tenant_id="t1", user_id="owner",
            permissions=frozenset({"knowledge.manage"}),
        )
        reader = KnowledgePrincipal(tenant_id="t1", user_id="reader")
        await service.grant_document_access(
            principal=owner, document_id=document.document_id,
            principal_type="user", principal_id="reader", permission="read",
        )
        self.assertEqual(
            len(await service.retrieve(principal=reader, query="restricted")), 1,
        )
        with self.assertRaises(PermissionError):
            await service.revoke_document_access(
                principal=reader, document_id=document.document_id,
                principal_type="user", principal_id="reader",
            )
        self.assertTrue(await service.revoke_document_access(
            principal=owner, document_id=document.document_id,
            principal_type="user", principal_id="reader",
        ))
        self.assertEqual(
            await service.retrieve(principal=reader, query="restricted"), [],
        )

    async def test_tenant_isolation(self):
        service=KnowledgeService(InMemoryKnowledgeRepository())
        await service.ingest(document=Document(tenant_id="t2",title="secret",source_uri="x",content_hash="x"),content=b"confidential")
        self.assertEqual(await service.retrieve(principal=KnowledgePrincipal(tenant_id="t1",user_id="u1"),query="confidential"),[])
    async def test_sync_document_parser_runs_off_the_event_loop_thread(self):
        event_loop_thread = threading.get_ident()

        class ThreadCheckingParser:
            supported_media_types = ("application/x-thread-test",)

            def parse(self, content, media_type):
                self.parser_thread = threading.get_ident()
                return content.decode("utf-8")

        from agent_platform.knowledge.ingestion import ParserRegistry

        parser = ThreadCheckingParser()
        registry = ParserRegistry(parsers=(parser,))
        service = KnowledgeService(InMemoryKnowledgeRepository(), parsers=registry)
        document = Document(
            tenant_id="tenant-thread", title="threaded parse",
            source_uri="file://threaded", content_hash="pending",
            media_type="application/x-thread-test",
        )
        result = await service.ingest(document=document, content=b"threaded parser result")
        self.assertEqual(result.chunks[0].text, "threaded parser result")
        self.assertNotEqual(parser.parser_thread, event_loop_thread)

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

    async def test_query_rewriter_output_is_type_and_size_bounded_fail_closed(self):
        class TrackingRepository(InMemoryKnowledgeRepository):
            def __init__(self):
                super().__init__()
                self.search_calls = 0

            async def search(self, **kwargs):
                self.search_calls += 1
                return await super().search(**kwargs)

        class Rewriter:
            def __init__(self, result):
                self.result = result

            async def rewrite(self, query):
                return self.result

        repository = TrackingRepository()
        principal = KnowledgePrincipal(tenant_id="tenant", user_id="user")
        for result, error in (
            ("", ValueError),
            ("x" * 21, ValueError),
            (None, TypeError),
            (42, TypeError),
        ):
            service = KnowledgeService(
                repository, query_rewriter=Rewriter(result), max_query_chars=20,
            )
            with self.subTest(result_type=type(result).__name__, result_length=len(result) if isinstance(result, str) else None):
                with self.assertRaises(error):
                    await service.retrieve(principal=principal, query="valid query")
        self.assertEqual(repository.search_calls, 0)

    async def test_retrieval_rejects_invalid_or_oversized_query_before_rewrite(self):
        class Rewriter:
            def __init__(self):
                self.calls = 0

            async def rewrite(self, query):
                self.calls += 1
                return query

        rewriter = Rewriter()
        service = KnowledgeService(
            InMemoryKnowledgeRepository(), query_rewriter=rewriter,
            max_query_chars=20,
        )
        principal = KnowledgePrincipal(tenant_id="tenant", user_id="user")
        with self.assertRaises(TypeError):
            await service.retrieve(principal=principal, query=None)
        with self.assertRaises(ValueError):
            await service.retrieve(principal=principal, query="x" * 21)
        with self.assertRaises(ValueError):
            KnowledgeService(InMemoryKnowledgeRepository(), max_query_chars=True)
        with self.assertRaises(ValueError):
            KnowledgeService(InMemoryKnowledgeRepository(), max_query_chars=0)
        self.assertEqual(rewriter.calls, 0)

    async def test_connector_ingestion_and_rag_evaluation(self):
        from agent_platform.knowledge.connectors import ConnectorDocument, ConnectorPage
        from agent_platform.knowledge.evaluation import RAGEvalCase, evaluate_retrieval
        class Connector:
            connector_id="fixture"
            async def list_changed(self,*,tenant_id,cursor,changed_since=None,limit=100):
                doc=Document(tenant_id=tenant_id,title="connector source",source_uri="connector://item/1",content_hash="x",scope=KnowledgeScope.PRIVATE,owner_user_id="u",metadata={"_knowledge_acl":[]})
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

    async def test_connector_sync_replaces_source_acl_and_enforces_empty_acl(self):
        from agent_platform.knowledge.connectors import ConnectorDocument, ConnectorPage

        class Connector:
            connector_id = "drive"
            async def list_changed(self, *, tenant_id, cursor, changed_since=None, limit=100):
                doc = Document(
                    tenant_id=tenant_id, title="restricted source",
                    source_uri="drive://item/1", content_hash="pending",
                    scope=KnowledgeScope.PRIVATE, owner_user_id="owner",
                    document_id="drive:item-1",
                    metadata={"_knowledge_acl": [
                        {"principal_type": "user", "principal_id": "reader-a", "permission": "read"},
                    ]},
                )
                return ConnectorPage(
                    items=(ConnectorDocument(document=doc, content=b"connector evidence"),),
                    next_cursor="cursor-1", has_more=False,
                )
            async def revoke(self, *, tenant_id, credential_ref):
                return None

        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository)
        await service.sync_connector(connector=Connector(), tenant_id="tenant-a")
        self.assertIn(("user", "reader-a", "read"), repository.document_acl[("tenant-a", "drive:item-1")])
        self.assertIn(("tenant-a", "drive:item-1"), repository.document_acl_enforced)

        # A subsequent source permission removal replaces, rather than appends to,
        # grants. An empty source ACL leaves the document owner-only.
        class RevokedConnector(Connector):
            async def list_changed(self, *, tenant_id, cursor, changed_since=None, limit=100):
                doc = Document(
                    tenant_id=tenant_id, title="restricted source",
                    source_uri="drive://item/1", content_hash="pending",
                    scope=KnowledgeScope.PRIVATE, owner_user_id="owner",
                    document_id="drive:item-1", metadata={"_knowledge_acl": []},
                )
                return ConnectorPage(
                    items=(ConnectorDocument(document=doc, content=b"connector evidence"),),
                    next_cursor="cursor-2", has_more=False,
                )

        await service.sync_connector(connector=RevokedConnector(), tenant_id="tenant-a")
        self.assertEqual(repository.document_acl[("tenant-a", "drive:item-1")], set())
        self.assertEqual(
            await service.retrieve(
                principal=KnowledgePrincipal(tenant_id="tenant-a", user_id="reader-a"),
                query="connector evidence",
            ),
            [],
        )

    async def test_connector_sync_rejects_unmapped_acl_instead_of_widening_access(self):
        from agent_platform.knowledge.connectors import ConnectorDocument, ConnectorPage

        class Connector:
            connector_id = "drive"
            async def list_changed(self, *, tenant_id, cursor, changed_since=None, limit=100):
                doc = Document(
                    tenant_id=tenant_id, title="unmapped", source_uri="drive://item/2",
                    content_hash="pending", scope=KnowledgeScope.PRIVATE,
                    owner_user_id="owner", document_id="drive:item-2",
                    metadata={"_knowledge_acl": [
                        {"principal_type": "external", "principal_id": "unknown", "permission": "read"},
                    ]},
                )
                return ConnectorPage(
                    items=(ConnectorDocument(document=doc, content=b"private evidence"),),
                    next_cursor="cursor-1", has_more=False,
                )
            async def revoke(self, *, tenant_id, credential_ref):
                return None

        service = KnowledgeService(InMemoryKnowledgeRepository())
        with self.assertRaises(ValueError):
            await service.sync_connector(connector=Connector(), tenant_id="tenant-a")
        self.assertIsNone(await service.repository.get_document("tenant-a", "drive:item-2"))
        self.assertIsNone(await service.repository.get_connector_cursor("tenant-a", "drive"))

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
        # The final chunk starts on page 1 and only overlaps the page 2 marker.
        self.assertEqual(chunks[-1].page_number,1)

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

    async def test_rag_answer_evaluation_measures_grounding_and_citations(self):
        from agent_platform.knowledge.evaluation import RAGAnswerEvalCase, evaluate_answers
        from agent_platform.knowledge.rag import RAGService
        knowledge=KnowledgeService(InMemoryKnowledgeRepository())
        doc=Document(tenant_id="t",title="source",source_uri="file://source",content_hash="x")
        await knowledge.ingest(document=doc,content=b"verified market evidence")
        class Generator:
            async def generate(self,*,query,context):
                return {"answer":"Verified market evidence supports the conclusion.", "citation_ids":[context[0]["citation_id"]]}
        rag=RAGService(knowledge=knowledge,generator=Generator())
        principal=KnowledgePrincipal(tenant_id="t",user_id="u")
        results=await evaluate_answers(rag,[RAGAnswerEvalCase(
            case_id="answer-1",query="market evidence",principal=principal,
            expected_answer_terms=frozenset({"verified","evidence"}),
            expected_citation_document_ids=frozenset({doc.document_id}),
        )])
        self.assertTrue(results[0].grounded)
        self.assertEqual(results[0].answer_term_coverage,1.0)
        self.assertEqual(results[0].citation_precision,1.0)
        self.assertEqual(results[0].citation_recall,1.0)


    async def test_rag_answer_emits_nested_retrieval_and_generation_traces(self):
        from contextlib import contextmanager

        from agent_platform.knowledge.rag import RAGService
        from agent_platform.observability.metrics import InMemoryMetricsRecorder

        class Span:
            def __init__(self):
                self.updates = []
            def update(self, **kwargs):
                self.updates.append(kwargs)

        class Observer:
            def __init__(self):
                self.calls = []
                self.spans = []
            @contextmanager
            def observe(self, *, name, input, metadata):
                span = Span()
                self.calls.append((name, input, metadata))
                self.spans.append(span)
                yield span

        class Generator:
            async def generate(self, *, query, context):
                return {
                    "answer": "The policy evidence is current.",
                    "citation_ids": [context[0]["citation_id"]],
                }

        service = KnowledgeService(InMemoryKnowledgeRepository())
        document = Document(
            tenant_id="tenant-trace", title="policy", source_uri="file://policy",
            content_hash="pending",
        )
        await service.ingest(document=document, content=b"current policy evidence")
        observer = Observer()
        metrics = InMemoryMetricsRecorder()
        rag = RAGService(
            knowledge=service, generator=Generator(),
            observer=observer, metrics=metrics,
        )
        answer = await rag.answer(
            principal=KnowledgePrincipal(tenant_id="tenant-trace", user_id="user-a"),
            query="where is the policy evidence",
            trace_context={"run_id": "run-rag", "request_id": "request-rag"},
        )
        self.assertFalse(answer.abstained)
        self.assertEqual(
            [call[0] for call in observer.calls],
            ["rag.answer", "rag.retrieve", "rag.generate"],
        )
        self.assertEqual(observer.calls[0][2]["run_id"], "run-rag")
        self.assertNotIn("where is the policy evidence", str(observer.calls[0][1]))
        self.assertEqual(observer.spans[0].updates[-1]["output"]["status"], "grounded")
        counters = metrics.snapshot()["counters"]
        self.assertIn('rag_requests_total{component="rag",status="grounded"}', counters)
        self.assertIn("rag_retrieval_hits", " ".join(metrics.snapshot()["observation_counts"]))


    async def test_scanned_pdf_can_use_explicit_ocr_extension(self):
        from unittest.mock import patch

        from agent_platform.knowledge.ingestion import OCRParserAdapter, ParserRegistry

        class EmptyPdfParser:
            supported_media_types = ("application/pdf",)

            def parse(self, content, media_type):
                return ""

        class FakeOCR:
            def __init__(self):
                self.calls = []

            def extract_text(self, content, media_type):
                self.calls.append((content, media_type))
                return "[page:1] scanned invoice total 42"

        ocr = FakeOCR()
        # Keep this test independent of optional pypdf installation and prove
        # that the registry falls through from an empty PDF text layer.
        with patch("agent_platform.knowledge.office_parsers.PDFParser", EmptyPdfParser):
            registry = ParserRegistry(parsers=(OCRParserAdapter(ocr),))
        service = KnowledgeService(InMemoryKnowledgeRepository(), parsers=registry)
        document = Document(
            tenant_id="tenant-ocr", title="scanned invoice",
            source_uri="file://invoice.pdf", content_hash="pending",
            media_type="application/pdf",
        )
        result = await service.ingest(document=document, content=b"%PDF-scanned")
        self.assertIn("scanned invoice total 42", result.chunks[0].text)
        self.assertEqual(ocr.calls, [(b"%PDF-scanned", "application/pdf")])

    async def test_ocr_extension_rejects_non_text_provider_result(self):
        from agent_platform.knowledge.ingestion import OCRParserAdapter

        class InvalidOCR:
            def extract_text(self, content, media_type):
                return None

        with self.assertRaises(TypeError):
            OCRParserAdapter(InvalidOCR()).parse(b"image", "image/png")


    async def test_versioned_rag_evaluation_fixture_covers_recall_citations_and_abstention(self):
        import json
        from pathlib import Path

        from agent_platform.knowledge.evaluation import (
            RAGAnswerEvalCase,
            RAGEvalCase,
            evaluate_answers,
            evaluate_retrieval,
        )
        from agent_platform.knowledge.rag import RAGService

        fixture_path = Path(__file__).parent / "fixtures" / "knowledge_rag_eval_v1.json"
        dataset = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.assertEqual(dataset["dataset_id"], "platform-knowledge-smoke-v1")
        service = KnowledgeService(InMemoryKnowledgeRepository())
        document_ids = {}
        for item in dataset["documents"]:
            document = Document(
                tenant_id="rag-eval-tenant", title=item["title"],
                source_uri=item["source_uri"], content_hash="pending",
            )
            result = await service.ingest(
                document=document, content=item["content"].encode("utf-8"),
            )
            document_ids[item["key"]] = result.document.document_id

        principal = KnowledgePrincipal(tenant_id="rag-eval-tenant", user_id="eval-user")
        retrieval_cases = []
        answer_cases = []
        answers = {}
        for item in dataset["cases"]:
            expected = frozenset(
                document_ids[key] for key in item["expected_document_keys"]
            )
            retrieval_cases.append(RAGEvalCase(
                case_id=item["case_id"], query=item["query"],
                expected_document_ids=expected, principal=principal,
            ))
            answer_cases.append(RAGAnswerEvalCase(
                case_id=item["case_id"], query=item["query"], principal=principal,
                expected_answer_terms=frozenset(item["expected_answer_terms"]),
                expected_citation_document_ids=expected,
                expect_abstain=item["expect_abstain"],
            ))
            answers[item["query"]] = item["answer"]

        class FixtureGenerator:
            async def generate(self, *, query, context):
                return {
                    "answer": answers[query],
                    "citation_ids": [str(item["citation_id"]) for item in context],
                }

        rag = RAGService(knowledge=service, generator=FixtureGenerator())
        retrieval = await evaluate_retrieval(service, retrieval_cases, k=3)
        answer_results = await evaluate_answers(rag, answer_cases, k=3)
        self.assertEqual(len(retrieval), 3)
        self.assertTrue(all(result.recall_at_k == 1.0 for result in retrieval))
        self.assertTrue(all(result.citation_coverage == 1.0 for result in retrieval[:2]))
        self.assertTrue(all(result.reciprocal_rank == 1.0 for result in retrieval[:2]))
        self.assertTrue(all(result.ndcg_at_k == 1.0 for result in retrieval[:2]))
        self.assertTrue(all(result.precision_at_k > 0.0 for result in retrieval[:2]))
        self.assertEqual(retrieval[-1].precision_at_k, 1.0)
        self.assertEqual(retrieval[-1].ndcg_at_k, 1.0)
        self.assertTrue(all(result.grounded for result in answer_results))
        self.assertTrue(answer_results[-1].abstained)


    async def test_office_parsers_extract_table_cells_from_docx_xlsx_and_pptx(self):
        import io
        try:
            import openpyxl
            from docx import Document as WordDocument
            from pptx import Presentation
            from pptx.util import Inches
        except ImportError:
            self.skipTest("optional Office parser dependencies are not installed")
        from agent_platform.knowledge.office_parsers import DOCXParser, PPTXParser, XLSXParser

        word = WordDocument()
        word.add_paragraph("Quarterly report")
        table = word.add_table(rows=2, cols=2)
        table.cell(0, 0).text = "Metric"
        table.cell(0, 1).text = "Value"
        table.cell(1, 0).text = "Revenue"
        table.cell(1, 1).text = "125"
        word_bytes = io.BytesIO()
        word.save(word_bytes)
        docx_text = DOCXParser().parse(word_bytes.getvalue(), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        self.assertIn("Quarterly report", docx_text)
        self.assertIn("Metric | Value", docx_text)
        self.assertIn("Revenue | 125", docx_text)

        workbook = openpyxl.Workbook()
        sheet = workbook.active
        sheet.title = "Forecast"
        sheet.append(["Metric", "Value"])
        sheet.append(["Revenue", 125])
        xlsx_bytes = io.BytesIO()
        workbook.save(xlsx_bytes)
        workbook.close()
        xlsx_text = XLSXParser().parse(xlsx_bytes.getvalue(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.assertIn("[sheet:Forecast]", xlsx_text)
        self.assertIn("Metric | Value", xlsx_text)
        self.assertIn("Revenue | 125", xlsx_text)

        presentation = Presentation()
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(5), Inches(1))
        ppt_table = slide.shapes[-1].table
        ppt_table.cell(0, 0).text = "Metric"
        ppt_table.cell(0, 1).text = "Value"
        ppt_table.cell(1, 0).text = "Revenue"
        ppt_table.cell(1, 1).text = "125"
        pptx_bytes = io.BytesIO()
        presentation.save(pptx_bytes)
        pptx_text = PPTXParser().parse(pptx_bytes.getvalue(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")
        self.assertIn("[slide:1]", pptx_text)
        self.assertIn("Metric | Value", pptx_text)
        self.assertIn("Revenue | 125", pptx_text)


    async def test_retrieval_emits_redacted_trace_and_low_cardinality_metrics(self):
        from contextlib import contextmanager

        from agent_platform.observability.metrics import InMemoryMetricsRecorder

        class Span:
            def __init__(self):
                self.updates = []
            def update(self, **kwargs):
                self.updates.append(kwargs)

        class Observer:
            def __init__(self):
                self.calls = []
                self.span = Span()
            @contextmanager
            def observe(self, **kwargs):
                self.calls.append(kwargs)
                yield self.span

        observer = Observer()
        metrics = InMemoryMetricsRecorder()
        service = KnowledgeService(
            InMemoryKnowledgeRepository(), observer=observer, metrics=metrics,
        )
        await service.retrieve(
            principal=KnowledgePrincipal(tenant_id="tenant-trace", user_id="u1"),
            query="private search phrase",
        )
        self.assertEqual(observer.calls[0]["name"], "knowledge.retrieve")
        self.assertEqual(observer.calls[0]["input"], {"query_chars": 21, "limit": 8})
        self.assertNotIn("private search phrase", str(observer.calls))
        self.assertEqual(observer.calls[0]["metadata"]["tenant_id"], "tenant-trace")
        self.assertTrue(any("result_count" in item.get("output", {}) for item in observer.span.updates))
        snapshot = metrics.snapshot()
        self.assertIn('knowledge_retrieval_total{component="knowledge",status="succeeded"}',
                      snapshot["counters"])


    async def test_sync_parser_runs_off_the_async_worker_thread(self):
        class ThreadRecordingParser:
            supported_media_types = ("application/x-thread-test",)

            def __init__(self):
                self.thread_id = None

            def parse(self, content, media_type):
                self.thread_id = threading.get_ident()
                return content.decode("utf-8")

        parser = ThreadRecordingParser()
        service = KnowledgeService(
            InMemoryKnowledgeRepository(),
            parsers=ParserRegistry(parsers=(parser,)),
        )
        main_thread = threading.get_ident()
        document = Document(
            tenant_id="thread-test", title="threaded",
            source_uri="memory://threaded", content_hash="pending",
            media_type="application/x-thread-test",
        )
        await service.ingest(document=document, content=b"threaded parser")
        self.assertNotEqual(parser.thread_id, main_thread)


    async def test_cleaning_is_canonical_before_hashing_and_chunking(self):
        from agent_platform.knowledge.chunking import clean_document_text, sha256_text

        composed = "Cafe\u0301\r\n\talpha\x00\ufeff  beta\n\n\n gamma "
        canonical = "Café\nalpha beta\n\ngamma"
        self.assertEqual(clean_document_text(composed), canonical)
        self.assertEqual(sha256_text(clean_document_text(composed)), sha256_text(canonical))
        with self.assertRaises(TypeError):
            clean_document_text(None)

    async def test_chunk_provenance_tracks_page_and_exact_cleaned_offsets(self):
        from agent_platform.knowledge.chunking import chunk_document, clean_document_text

        document = Document(
            tenant_id="tenant-c10-03", title="paged source",
            source_uri="s3://bucket/report.pdf", content_hash="pending",
            document_id="report-1",
        )
        source = "[page:1] " + ("first page evidence " * 8) + "\n[page:2] " + ("second page evidence " * 8)
        normalized = clean_document_text(source)
        chunks = chunk_document(document, source, max_chars=120, overlap=12)

        self.assertGreaterEqual(len(chunks), 2)
        self.assertEqual(chunks[0].page_number, 1)
        self.assertTrue(any(chunk.page_number == 2 for chunk in chunks))
        for chunk in chunks:
            self.assertEqual(
                normalized[chunk.start_offset:chunk.end_offset],
                chunk.text,
            )
            self.assertEqual(chunk.metadata["source_uri"], document.source_uri)
            self.assertEqual(chunk.metadata["cleaning_version"], "knowledge-clean-v1")
            self.assertEqual(
                chunk.metadata["source_locator"],
                {
                    "kind": "text_offset",
                    "start_offset": chunk.start_offset,
                    "end_offset": chunk.end_offset,
                    "page_number": chunk.page_number,
                },
            )

    async def test_chunk_ids_and_hashes_are_repeatable_but_version_scoped(self):
        from dataclasses import replace

        from agent_platform.knowledge.chunking import chunk_document, sha256_text

        document = Document(
            tenant_id="tenant-c10-03", title="stable source",
            source_uri="file://stable", content_hash="pending",
            document_id="stable-1",
        )
        text = "stable paragraph. " * 30
        first = chunk_document(document, text, max_chars=140, overlap=20)
        retry = chunk_document(document, text, max_chars=140, overlap=20)
        next_version = chunk_document(
            replace(document, version=2), text, max_chars=140, overlap=20,
        )
        self.assertEqual([item.chunk_id for item in first], [item.chunk_id for item in retry])
        self.assertEqual([item.content_hash for item in first], [sha256_text(item.text) for item in first])
        self.assertNotEqual(
            [item.chunk_id for item in first],
            [item.chunk_id for item in next_version],
        )


    async def test_chunk_update_invalidates_embedding_and_cache(self):
        class Embedder:
            model_id = "fixture"
            dimensions = 2

            async def embed_query(self, text):
                return (1.0, 0.0)

            async def embed_documents(self, texts):
                return [(1.0, 0.0) for _ in texts]

        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository, embedder=Embedder())
        document = Document(
            tenant_id="tenant-c10-03", title="editable",
            source_uri="memory://editable", content_hash="pending",
        )
        result = await service.ingest(document=document, content=b"original evidence")
        chunk = result.chunks[0]
        self.assertIn(("tenant-c10-03", chunk.chunk_id), repository.embeddings)

        updated = await repository.update_chunk(
            "tenant-c10-03", document.document_id, 1,
            chunk.chunk_id, "replacement evidence",
        )

        self.assertEqual(updated.text, "replacement evidence")
        self.assertNotIn(("tenant-c10-03", chunk.chunk_id), repository.embeddings)
        self.assertEqual(
            repository.cache_invalidations[-1],
            {
                "tenant_id": "tenant-c10-03", "resource_type": "chunk",
                "resource_id": chunk.chunk_id, "reason": "updated",
            },
        )

    async def test_document_delete_removes_all_derived_data_for_every_version(self):
        class Embedder:
            model_id = "fixture"
            dimensions = 2

            async def embed_query(self, text):
                return (1.0, 0.0)

            async def embed_documents(self, texts):
                return [(1.0, 0.0) for _ in texts]

        from dataclasses import replace

        repository = InMemoryKnowledgeRepository()
        service = KnowledgeService(repository, embedder=Embedder())
        original = Document(
            tenant_id="tenant-c10-03", title="deletable",
            source_uri="memory://deletable", content_hash="pending",
            document_id="delete-all-versions",
        )
        first = await service.ingest(document=original, content=b"first searchable version")
        second = await service.ingest(
            document=replace(original, version=2),
            content=b"second searchable version",
        )
        owner = KnowledgePrincipal(
            tenant_id="tenant-c10-03", user_id="owner",
            permissions=frozenset({"knowledge.manage"}),
        )
        await service.grant_document_access(
            principal=owner, document_id=original.document_id,
            principal_type="user", principal_id="reader",
        )
        await service.grant_chunk_access(
            principal=owner, document_id=original.document_id,
            chunk_id=second.chunks[0].chunk_id,
            principal_type="user", principal_id="reader",
        )
        self.assertTrue(repository.embeddings)

        self.assertTrue(await service.delete_document(
            principal=owner, document_id=original.document_id,
        ))

        self.assertIsNone(await repository.get_document(
            "tenant-c10-03", original.document_id,
        ))
        self.assertFalse(any(
            key[0] == "tenant-c10-03" and key[1] == original.document_id
            for key in repository.chunks
        ))
        self.assertFalse(any(
            tenant_id == "tenant-c10-03"
            and chunk_id in {chunk.chunk_id for chunk in (*first.chunks, *second.chunks)}
            for tenant_id, chunk_id in repository.embeddings
        ))
        self.assertNotIn(
            ("tenant-c10-03", original.document_id), repository.document_acl,
        )
        self.assertFalse(any(
            key[0] == "tenant-c10-03"
            and key[1] in {chunk.chunk_id for chunk in (*first.chunks, *second.chunks)}
            for key in repository.chunk_acl
        ))
        invalidated = {
            (item["resource_type"], item["resource_id"], item["reason"])
            for item in repository.cache_invalidations
        }
        self.assertIn(("document", original.document_id, "deleted"), invalidated)
        for chunk in (*first.chunks, *second.chunks):
            self.assertIn(("chunk", chunk.chunk_id, "deleted"), invalidated)
