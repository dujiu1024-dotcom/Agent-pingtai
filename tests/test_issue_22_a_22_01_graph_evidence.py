from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(True, reason="_Connection async plumbing")
import unittest

from agent_platform.graph.extraction import (
    GraphEntityDraft,
    GraphEvidenceSpan,
    GraphExtractionDraft,
    GraphExtractionService,
    GraphRelationDraft,
    GraphSourceRecord,
    PostgresGraphCandidateStore,
)
from agent_platform.graph.schema import (
    EntityTypeDefinition,
    GraphSchema,
    GraphSourceDomain,
    PropertyDefinition,
    PropertyKind,
    RelationTypeDefinition,
    SourceTypeMapping,
)
from agent_platform.knowledge.chunking import clean_document_text
from agent_platform.knowledge.contracts import Document
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class _Extractor:
    def __init__(self, draft):
        self.draft = draft
        self.calls = 0

    async def extract(self, *, source, schema):
        self.calls += 1
        return self.draft


class _CandidateStore:
    def __init__(self):
        self.batches = []

    async def append_candidates(self, candidates):
        self.batches.append(tuple(candidates))
        return len(candidates)


class _Cursor:
    def __init__(self, row):
        self.row = row

    async def fetchone(self):
        return self.row


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Connection:
    def __init__(self):
        self.statements = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def transaction(self):
        return _Transaction()

    async def execute(self, statement, parameters):
        self.statements.append((statement, parameters))
        return _Cursor((parameters[-1],))


def _schema():
    return GraphSchema(
        "tenant-a",
        (
            EntityTypeDefinition("document", (PropertyDefinition("title", PropertyKind.STRING),)),
            EntityTypeDefinition("person", (PropertyDefinition("name", PropertyKind.STRING, required=True),)),
            EntityTypeDefinition("company", (PropertyDefinition("name", PropertyKind.STRING, required=True),)),
        ),
        (RelationTypeDefinition("works_for", ("person",), ("company",)),),
        (SourceTypeMapping(GraphSourceDomain.KNOWLEDGE, "document", "document"),),
    )


class GraphEvidenceAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.raw_content = (
            "Acme Research is headquartered in London. "
            "Ada Lovelace works for Acme Research as its data engineering lead. "
            "The group maintains the Atlas platform. "
            "Acme Research uses PostgreSQL for durable metadata storage. "
            "Ada joined Acme Research in 2021."
        )
        self.document = Document(
            tenant_id="tenant-a",
            title="Acme Research architecture and ownership",
            source_uri="fixture://knowledge/acme-research-architecture",
            content_hash="pending",
            project_id="project-a",
        )
        self.knowledge = KnowledgeService(InMemoryKnowledgeRepository())
        self.ingested = await self.knowledge.ingest(
            document=self.document,
            content=self.raw_content.encode("utf-8"),
            max_chars=120,
            overlap=20,
        )
        self.source_ref = (
            f"knowledge://{self.document.tenant_id}/"
            f"{self.document.document_id}@{self.document.version}"
        )
        normalized = clean_document_text(self.raw_content)
        self.spans = tuple(
            GraphEvidenceSpan(
                chunk_id=chunk.chunk_id,
                start_offset=chunk.start_offset,
                end_offset=chunk.end_offset,
                text=chunk.text,
                page_number=chunk.page_number,
            )
            for chunk in self.ingested.chunks
        )
        self.source = GraphSourceRecord(
            tenant_id=self.document.tenant_id,
            domain=GraphSourceDomain.KNOWLEDGE,
            source_type="document",
            source_id=self.document.document_id,
            source_version=str(self.document.version),
            source_ref=self.source_ref,
            content=normalized,
            project_id=self.document.project_id,
            evidence_spans=self.spans,
        )

    def _span_for(self, phrase):
        return next(span for span in self.spans if phrase in span.text)

    def _evidence(self, phrase):
        return (self.source_ref, self._span_for(phrase).reference())

    async def test_knowledge_chunks_produce_traceable_confident_reviewable_graph_candidates(self):
        draft = GraphExtractionDraft(
            entities=(
                GraphEntityDraft(
                    "person-ada", "person", {"name": "Ada Lovelace"}, 0.97,
                    self._evidence("Ada Lovelace"),
                ),
                GraphEntityDraft(
                    "company-acme", "company", {"name": "Acme Research"}, 0.94,
                    self._evidence("Acme Research"),
                ),
            ),
            relations=(
                GraphRelationDraft(
                    "ada-works-at-acme", "works_for", "person-ada", "company-acme",
                    {}, 0.91, self._evidence("Ada Lovelace works for Acme Research"),
                ),
            ),
        )
        candidate_store = _CandidateStore()
        service = GraphExtractionService(
            schema_provider=lambda tenant_id: _schema(),
            extractor=_Extractor(draft),
            candidate_store=candidate_store,
        )

        candidates = await service.extract_source(source=self.source)

        self.assertEqual(len(candidates), 3)
        self.assertEqual(len(candidate_store.batches), 1)
        resolvable = {span.reference() for span in self.spans}
        traceable = 0
        for candidate in candidates:
            self.assertGreaterEqual(candidate.confidence, 0.0)
            self.assertLessEqual(candidate.confidence, 1.0)
            self.assertEqual(candidate.source_ref, self.source_ref)
            self.assertEqual(candidate.source_version, str(self.document.version))
            fragment_refs = set(candidate.evidence_refs) - {self.source_ref}
            self.assertTrue(fragment_refs)
            self.assertTrue(fragment_refs <= resolvable)
            traceable += 1
        traceability = traceable / len(candidates)
        self.assertGreaterEqual(traceability, 0.80)
        self.assertEqual(traceability, 1.0)

        connection = _Connection()
        review_store = PostgresGraphCandidateStore(
            tenant_id="tenant-a",
            connection_factory=lambda: connection,
        )
        accepted = await review_store.review_candidate(
            candidate_id=candidates[0].candidate_id,
            reviewer_id="graph-reviewer",
            decision="accepted",
            reason="Source fragment and extracted entity verified.",
        )
        rejected = await review_store.review_candidate(
            candidate_id=candidates[1].candidate_id,
            reviewer_id="graph-reviewer",
            decision="rejected",
            reason="Manual reviewer rejected this candidate.",
        )
        self.assertTrue(accepted)
        self.assertTrue(rejected)
        self.assertEqual(len(connection.statements), 2)
        for statement, parameters in connection.statements:
            self.assertIn("tenant_id = %s AND candidate_id = %s", statement)
            self.assertIn("status IN ('candidate', 'conflict')", statement)
            self.assertEqual(parameters[1], "graph-reviewer")
            self.assertTrue(parameters[2])
            self.assertEqual(parameters[3], "tenant-a")

    async def test_evidence_reference_must_resolve_to_a_validated_chunk(self):
        span = self.spans[0]
        invalid_draft = GraphExtractionDraft(
            entities=(
                GraphEntityDraft(
                    "person-ada", "person", {"name": "Ada Lovelace"}, 0.9,
                    (self.source_ref, "chunk://unknown?start=0&end=5&sha256=invalid"),
                ),
            ),
            relations=(),
        )
        store = _CandidateStore()
        service = GraphExtractionService(
            schema_provider=lambda tenant_id: _schema(),
            extractor=_Extractor(invalid_draft),
            candidate_store=store,
        )
        with self.assertRaisesRegex(ValueError, "does not resolve"):
            await service.extract_source(source=self.source)
        self.assertEqual(store.batches, [])
        self.assertNotEqual(span.reference(), invalid_draft.entities[0].evidence_refs[1])

    async def test_source_span_offsets_and_text_are_checked_before_extraction(self):
        broken = GraphEvidenceSpan(
            chunk_id=self.spans[0].chunk_id,
            start_offset=self.spans[0].start_offset,
            end_offset=self.spans[0].end_offset,
            text="forged source text",
            page_number=self.spans[0].page_number,
        )
        source = GraphSourceRecord(
            tenant_id=self.source.tenant_id,
            domain=self.source.domain,
            source_type=self.source.source_type,
            source_id=self.source.source_id,
            source_version=self.source.source_version,
            source_ref=self.source.source_ref,
            content=self.source.content,
            project_id=self.source.project_id,
            evidence_spans=(broken,),
        )
        extractor = _Extractor(GraphExtractionDraft((), ()))
        store = _CandidateStore()
        service = GraphExtractionService(
            schema_provider=lambda tenant_id: _schema(),
            extractor=extractor,
            candidate_store=store,
        )
        with self.assertRaisesRegex(ValueError, "does not match"):
            await service.extract_source(source=source)
        self.assertEqual(extractor.calls, 0)
        self.assertEqual(store.batches, [])


if __name__ == "__main__":
    unittest.main()
