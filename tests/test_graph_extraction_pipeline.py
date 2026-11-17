from __future__ import annotations

import unittest

from agent_platform.graph.pipeline import (
    GraphCandidateStatus, GraphExtractionDraft, GraphExtractionPipeline,
    GraphPrincipal, GraphSourceRecord, InMemoryGraphCandidateRepository,
)
from agent_platform.graph.schema import GraphEntity, GraphSourceDomain, default_graph_schema


class Extractor:
    def __init__(self, drafts):
        self.drafts, self.calls = drafts, 0

    async def extract(self, *, source, schema):
        self.calls += 1
        return self.drafts


class GraphStore:
    def __init__(self):
        self.entities = {}

    async def upsert_entity(self, entity, *, name, source_ref=None, allowed_project_ids=()):
        if entity.project_id is not None and entity.project_id not in allowed_project_ids:
            raise PermissionError("project access denied")
        self.entities[(entity.tenant_id, entity.entity_id)] = (entity, name, source_ref)

    async def upsert_relation(self, relation, *, allowed_project_ids=()):
        return None

    async def delete_entity(self, entity_id, *, allowed_project_ids=()):
        return self.entities.pop(("tenant-a", entity_id), None) is not None


class GraphExtractionPipelineTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.schema = default_graph_schema("tenant-a")
        self.candidates = InMemoryGraphCandidateRepository()
        self.graph = GraphStore()
        self.principal = GraphPrincipal(
            tenant_id="tenant-a", user_id="reviewer", project_ids=frozenset({"project-a"}),
            manage_project_ids=frozenset({"project-a"}), permissions=frozenset({"graph.extract"}),
        )
        self.source = GraphSourceRecord(
            tenant_id="tenant-a", domain=GraphSourceDomain.KNOWLEDGE, source_type="document",
            source_id="doc-1", source_version=2,
            content="The project uses PostgreSQL for durable storage.", project_id="project-a",
            display_name="Architecture",
        )

    def draft(self, *, entity_id="doc-1", title="Architecture", confidence=0.9, conflict_key=None):
        entity = GraphEntity(tenant_id="tenant-a", entity_id=entity_id, entity_type="document",
                             attributes={"title": title}, project_id="project-a")
        return GraphExtractionDraft(entity=entity, name=title, confidence=confidence, conflict_key=conflict_key)

    async def test_propose_is_candidate_only_and_confirmation_persists_source_lineage(self):
        pipeline = GraphExtractionPipeline(
            schema=self.schema, extractor=Extractor([self.draft()]),
            candidates=self.candidates, graph=self.graph,
        )
        candidate = (await pipeline.propose(source=self.source, principal=self.principal))[0]
        self.assertEqual(candidate.status, GraphCandidateStatus.PENDING)
        self.assertEqual(self.graph.entities, {})
        self.assertEqual(candidate.source_reference.source_id, "doc-1")
        confirmed = await pipeline.confirm(principal=self.principal, candidate_id=candidate.candidate_id)
        self.assertEqual(confirmed.status, GraphCandidateStatus.CONFIRMED)
        self.assertEqual(self.graph.entities[("tenant-a", "doc-1")][2].source_version, 2)

    async def test_low_confidence_requires_explicit_review_and_never_auto_persists(self):
        pipeline = GraphExtractionPipeline(
            schema=self.schema, extractor=Extractor([self.draft(confidence=0.2)]),
            candidates=self.candidates, graph=self.graph, minimum_confidence=0.6,
        )
        candidate = (await pipeline.propose(source=self.source, principal=self.principal))[0]
        self.assertEqual(candidate.status, GraphCandidateStatus.REVIEW_REQUIRED)
        self.assertEqual(self.graph.entities, {})
        confirmed = await pipeline.confirm(principal=self.principal, candidate_id=candidate.candidate_id)
        self.assertEqual(confirmed.status, GraphCandidateStatus.CONFIRMED)

    async def test_conflicts_require_explicit_resolution_and_supersede_old_projection(self):
        first = GraphExtractionPipeline(
            schema=self.schema,
            extractor=Extractor([self.draft(title="First", conflict_key="doc-policy")]),
            candidates=self.candidates, graph=self.graph,
        )
        first_candidate = (await first.propose(source=self.source, principal=self.principal))[0]
        await first.confirm(principal=self.principal, candidate_id=first_candidate.candidate_id)
        next_source = GraphSourceRecord(
            tenant_id="tenant-a", domain=GraphSourceDomain.KNOWLEDGE, source_type="document",
            source_id="doc-2", source_version=1, content="Updated evidence.", project_id="project-a",
        )
        second = GraphExtractionPipeline(
            schema=self.schema,
            extractor=Extractor([self.draft(entity_id="doc-2", title="Second", conflict_key="doc-policy")]),
            candidates=self.candidates, graph=self.graph,
        )
        candidate = (await second.propose(source=next_source, principal=self.principal))[0]
        self.assertEqual(candidate.status, GraphCandidateStatus.CONFLICT)
        resolved = await second.resolve_conflict(principal=self.principal, candidate_id=candidate.candidate_id)
        self.assertEqual(resolved.status, GraphCandidateStatus.CONFIRMED)
        old = await self.candidates.get("tenant-a", first_candidate.candidate_id)
        self.assertEqual(old.status, GraphCandidateStatus.SUPERSEDED)
        self.assertNotIn(("tenant-a", "doc-1"), self.graph.entities)

    async def test_rejects_cross_tenant_and_unmapped_sources_before_extraction(self):
        extractor = Extractor([self.draft()])
        pipeline = GraphExtractionPipeline(
            schema=self.schema, extractor=extractor, candidates=self.candidates, graph=self.graph,
        )
        foreign = GraphSourceRecord(
            tenant_id="tenant-b", domain=GraphSourceDomain.KNOWLEDGE, source_type="document",
            source_id="doc-x", source_version=1, content="secret", project_id="project-a",
        )
        with self.assertRaises(PermissionError):
            await pipeline.propose(source=foreign, principal=self.principal)
        self.assertEqual(extractor.calls, 0)
        unmapped = GraphSourceRecord(
            tenant_id="tenant-a", domain=GraphSourceDomain.KNOWLEDGE, source_type="unknown",
            source_id="doc-x", source_version=1, content="secret", project_id="project-a",
        )
        with self.assertRaisesRegex(ValueError, "not mapped"):
            await pipeline.propose(source=unmapped, principal=self.principal)
        self.assertEqual(extractor.calls, 0)

    async def test_project_membership_and_review_permissions_are_enforced(self):
        pipeline = GraphExtractionPipeline(
            schema=self.schema, extractor=Extractor([self.draft()]),
            candidates=self.candidates, graph=self.graph,
        )
        with self.assertRaises(PermissionError):
            await pipeline.propose(
                source=self.source,
                principal=GraphPrincipal(tenant_id="tenant-a", user_id="reader",
                                         permissions=frozenset({"graph.extract"})),
            )
        candidate = (await pipeline.propose(source=self.source, principal=self.principal))[0]
        with self.assertRaises(PermissionError):
            await pipeline.confirm(
                principal=GraphPrincipal(tenant_id="tenant-a", user_id="reader",
                                         project_ids=frozenset({"project-a"})),
                candidate_id=candidate.candidate_id,
            )

    async def test_candidate_ids_are_idempotent_for_same_source_revision(self):
        pipeline = GraphExtractionPipeline(
            schema=self.schema, extractor=Extractor([self.draft()]),
            candidates=self.candidates, graph=self.graph,
        )
        first = await pipeline.propose(source=self.source, principal=self.principal)
        second = await pipeline.propose(source=self.source, principal=self.principal)
        self.assertEqual(first[0].candidate_id, second[0].candidate_id)
        self.assertEqual(len(await self.candidates.list("tenant-a")), 1)


if __name__ == "__main__":
    unittest.main()
