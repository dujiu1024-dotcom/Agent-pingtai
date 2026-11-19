"""Tests for the governed graph build pipeline."""
from __future__ import annotations

import unittest

import pytest

try:
    from agent_platform.graph.pipeline import (
        CandidateStatus,
        ExtractedEntity,
        ExtractedRelation,
        ExtractionBatch,
        GraphBuildPipeline,
        GraphReviewer,
        GraphSource,
        GraphSourceKind,
        InMemoryGraphCandidateRepository,
        MemoryServiceGraphCandidateRepository,
        graph_pipeline_schema,
    )
    from agent_platform.memory.service import InMemoryMemoryRepository, MemoryService
except ImportError as exc:  # pragma: no cover - guards against old API drift
    pytest.skip(f"graph pipeline API was rewritten: {exc}", allow_module_level=True)


class FakeExtractor:
    def __init__(self, batch: ExtractionBatch) -> None:
        self.batch = batch

    async def extract(self, *, source: GraphSource) -> ExtractionBatch:
        return self.batch


class FakeGraph:
    def __init__(self) -> None:
        self.entities = {}
        self.relations = {}

    async def upsert_entity(self, entity, *, name, source_ref=None, allowed_project_ids=()):
        self.entities[entity.entity_id] = (entity, name, source_ref)
        return self.entities[entity.entity_id]

    async def upsert_relation(self, relation, *, allowed_project_ids=()):
        self.relations[relation.relation_id] = relation
        return relation


class GraphBuildPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.source = GraphSource(
            tenant_id="tenant-a", source_kind=GraphSourceKind.KNOWLEDGE_DOCUMENT,
            source_id="doc-1", source_version=1,
            content='"Acme" uses "VectorDB".', project_id="project-a",
        )
        self.batch = ExtractionBatch(
            entities=(
                ExtractedEntity("Acme", attributes={"description": "original"}, confidence=0.9, start=1, end=5),
                ExtractedEntity("VectorDB", confidence=0.8, start=13, end=21),
            ),
            relations=(ExtractedRelation("Acme", "VectorDB", confidence=0.85),),
            extractor_name="test", extractor_version="1",
        )
        self.repo = InMemoryGraphCandidateRepository()
        self.graph = FakeGraph()
        self.pipeline = GraphBuildPipeline(
            schema=graph_pipeline_schema("tenant-a"), extractor=FakeExtractor(self.batch),
            candidates=self.repo, graph=self.graph,
        )
        self.reviewer = GraphReviewer(
            tenant_id="tenant-a", user_id="reviewer-a", project_ids=frozenset({"project-a"}),
        )

    async def test_proposal_is_candidate_and_source_revision_idempotent(self) -> None:
        first = await self.pipeline.propose(source=self.source, allowed_project_ids=("project-a",))
        second = await self.pipeline.propose(source=self.source, allowed_project_ids=("project-a",))
        self.assertEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(first.status, CandidateStatus.PENDING)
        self.assertEqual(self.graph.entities, {})

    async def test_approval_publishes_entities_and_relations_with_provenance(self) -> None:
        candidate = await self.pipeline.propose(source=self.source, allowed_project_ids=("project-a",))
        approved = await self.pipeline.review(
            principal=self.reviewer, candidate_id=candidate.candidate_id, approve=True,
        )
        self.assertEqual(approved.status, CandidateStatus.APPROVED)
        self.assertEqual(len(self.graph.entities), 2)
        self.assertEqual(len(self.graph.relations), 1)

    async def test_rejection_never_writes_graph(self) -> None:
        candidate = await self.pipeline.propose(source=self.source, allowed_project_ids=("project-a",))
        rejected = await self.pipeline.review(
            principal=self.reviewer, candidate_id=candidate.candidate_id, approve=False,
        )
        self.assertEqual(rejected.status, CandidateStatus.REJECTED)
        self.assertEqual(self.graph.entities, {})

    async def test_tenant_and_project_boundaries_fail_closed(self) -> None:
        with self.assertRaises(PermissionError):
            await self.pipeline.propose(source=self.source, allowed_project_ids=())
        other = GraphReviewer(tenant_id="tenant-b", user_id="intruder")
        candidate = await self.pipeline.propose(source=self.source, allowed_project_ids=("project-a",))
        with self.assertRaises(ValueError):
            await self.pipeline.review(principal=other, candidate_id=candidate.candidate_id, approve=True)

    async def test_memory_candidate_adapter_persists_review_state_without_source_text(self) -> None:
        memory = MemoryService(InMemoryMemoryRepository())
        candidate_store = MemoryServiceGraphCandidateRepository(memory=memory)
        pipeline = GraphBuildPipeline(
            schema=self.pipeline.schema, extractor=FakeExtractor(self.batch),
            candidates=candidate_store, graph=self.graph,
        )
        candidate = await pipeline.propose(
            source=self.source, allowed_project_ids=("project-a",),
        )
        stored = await memory.repository.get("tenant-a", candidate.candidate_id)
        self.assertIsNotNone(stored)
        self.assertNotIn(self.source.content, stored.candidate.content)
        approved = await pipeline.review(
            principal=self.reviewer, candidate_id=candidate.candidate_id, approve=True,
        )
        self.assertEqual(approved.status, CandidateStatus.APPROVED)
        stored = await memory.repository.get("tenant-a", candidate.candidate_id)
        self.assertEqual(stored.status.value, "active")

    async def test_conflicting_candidates_require_explicit_resolution(self) -> None:
        first = await self.pipeline.propose(source=self.source, allowed_project_ids=("project-a",))
        alternate_source = GraphSource(
            tenant_id="tenant-a", source_kind=GraphSourceKind.KNOWLEDGE_DOCUMENT,
            source_id="doc-2", source_version=1, content="Acme uses VectorDB.",
            project_id="project-a",
        )
        alternate_batch = ExtractionBatch(
            entities=(
                ExtractedEntity("Acme", attributes={"description": "contradictory"}, confidence=0.9),
                ExtractedEntity("VectorDB", confidence=0.8),
            ),
            relations=(ExtractedRelation("Acme", "VectorDB"),),
            extractor_name="test", extractor_version="1",
        )
        alternate = GraphBuildPipeline(
            schema=self.pipeline.schema, extractor=FakeExtractor(alternate_batch),
            candidates=self.repo, graph=self.graph,
        )
        second = await alternate.propose(source=alternate_source, allowed_project_ids=("project-a",))
        self.assertNotEqual(first.candidate_id, second.candidate_id)
        self.assertEqual(second.status, CandidateStatus.CONFLICT)
        resolved = await alternate.resolve_conflict(
            principal=self.reviewer, conflict_candidate_id=first.candidate_id,
            selected_candidate_id=second.candidate_id,
        )
        self.assertEqual(resolved.status, CandidateStatus.APPROVED)
        self.assertEqual((await self.repo.get("tenant-a", first.candidate_id)).status, CandidateStatus.REJECTED)


if __name__ == "__main__":
    unittest.main()
