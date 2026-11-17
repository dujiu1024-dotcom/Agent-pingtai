from __future__ import annotations

import hashlib
import unittest

from agent_platform.knowledge.citations import citation_context
from agent_platform.knowledge.contracts import Document, KnowledgePrincipal
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.retrieval import RetrievalStrategy
from agent_platform.knowledge.service import KnowledgeService


class RewriteQuery:
    async def rewrite(self, query: str) -> str:
        return f"ORBIT {query}"


class IdentityReranker:
    async def rerank(self, query, hits):
        return list(reversed(hits))


class ProvenanceGenerator:
    async def generate(self, *, query, context):
        return {"answer": "Grounded answer", "citation_ids": [context[0]["citation_id"]]}


class Issue10A1003ProvenanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_answer_citation_preserves_source_coordinates_and_retrieval_chain(self):
        repository = InMemoryKnowledgeRepository()
        knowledge = KnowledgeService(
            repository,
            query_rewriter=RewriteQuery(),
            reranker=IdentityReranker(),
        )
        source = (
            "[page:7] ORBIT retention policy: records are retained for 47 days. "
            "This paragraph provides enough source text for deterministic ingestion "
            "and citation provenance validation without relying on external services."
        )
        document = Document(
            tenant_id="tenant-a",
            document_id="orbit-policy",
            title="ORBIT retention policy",
            source_uri="https://docs.example.test/orbit-policy.pdf",
            content_hash="pending",
            media_type="text/plain",
        )
        ingested = await knowledge.ingest(document=document, content=source.encode())
        self.assertEqual(len(ingested.chunks), 1)
        chunk = ingested.chunks[0]
        self.assertEqual(chunk.page_number, 7)
        self.assertEqual(chunk.metadata["source_locator"]["page_number"], 7)

        principal = KnowledgePrincipal(tenant_id="tenant-a", user_id="reader-a")
        rag = RAGService(knowledge=knowledge, generator=ProvenanceGenerator())
        answer = await rag.answer(
            principal=principal,
            query="retention",
            limit=4,
            strategy=RetrievalStrategy.LEXICAL,
            trace_context={"run_id": "run-123", "request_id": "request-456"},
        )

        self.assertFalse(answer.abstained)
        self.assertEqual(len(answer.retrieved), 1)
        hit = answer.retrieved[0]
        trace = hit.retrieval_trace
        self.assertEqual(hit.document_id, document.document_id)
        self.assertEqual(hit.document_version, 1)
        self.assertEqual(hit.chunk_id, chunk.chunk_id)
        self.assertEqual(hit.page_number, 7)
        self.assertEqual((hit.start_offset, hit.end_offset), (chunk.start_offset, chunk.end_offset))
        self.assertTrue(trace["retrieval_id"])
        self.assertEqual(trace["retrieval_id"], answer.citations[0]["retrieval_trace"]["retrieval_id"])
        self.assertEqual(trace["query_fingerprint"], hashlib.sha256(b"retention").hexdigest())
        self.assertEqual(trace["requested_strategy"], "lexical")
        self.assertEqual(trace["run_id"], "run-123")
        self.assertEqual(trace["request_id"], "request-456")
        self.assertEqual(trace["candidate_limit"], 20)
        self.assertEqual(trace["result_limit"], 4)
        self.assertEqual(trace["result_rank"], 1)
        self.assertTrue(trace["query_rewritten"])
        self.assertTrue(trace["reranker_applied"])
        self.assertTrue(trace["acl_filtered_before_ranking"])

        citation = answer.citations[0]
        self.assertEqual(citation["document_id"], document.document_id)
        self.assertEqual(citation["document_version"], 1)
        self.assertEqual(citation["chunk_id"], chunk.chunk_id)
        self.assertEqual(citation["source_uri"], document.source_uri)
        self.assertEqual(citation["source_locator"]["page_number"], 7)
        self.assertEqual(citation["source_locator"]["start_offset"], chunk.start_offset)
        self.assertEqual(citation["source_locator"]["end_offset"], chunk.end_offset)
        self.assertEqual(citation["retrieval_trace"], trace)

    async def test_citation_context_keeps_page_and_offset_for_page_less_sources(self):
        repository = InMemoryKnowledgeRepository()
        knowledge = KnowledgeService(repository)
        document = Document(
            tenant_id="tenant-a",
            document_id="plain-text",
            title="Plain text source",
            source_uri="file://plain-text",
            content_hash="pending",
        )
        await knowledge.ingest(
            document=document,
            content=b"Source locator offsets remain available even when the parser has no page metadata. " * 2,
        )
        hits = await knowledge.retrieve(
            principal=KnowledgePrincipal(tenant_id="tenant-a", user_id="reader"),
            query="source locator offsets",
            strategy=RetrievalStrategy.HYBRID,
        )
        self.assertTrue(hits)
        context = citation_context(hits)[0]
        self.assertIsNone(context["source_locator"]["page_number"])
        self.assertGreater(context["source_locator"]["end_offset"], context["source_locator"]["start_offset"])
        self.assertTrue(context["retrieval_trace"]["retrieval_id"])


if __name__ == "__main__":
    unittest.main()
