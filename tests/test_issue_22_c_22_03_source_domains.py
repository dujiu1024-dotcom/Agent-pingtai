from __future__ import annotations

import unittest

from agent_platform.graph.pipeline import (
    GraphCandidateStatus,
    GraphExtractionDraft,
    GraphExtractionPipeline,
    GraphPrincipal,
    GraphSourceRecord,
    InMemoryGraphCandidateRepository,
)
from agent_platform.graph.schema import GraphEntity, GraphSourceDomain, default_graph_schema


class DomainExtractor:
    def __init__(self, entity_type: str, entity_id: str):
        self.entity_type = entity_type
        self.entity_id = entity_id

    async def extract(self, *, source, schema):
        attributes = {
            "document": {"title": source.display_name or source.source_id},
            "memory": {"memory_scope": "project"},
            "blackboard_entry": {"entry_kind": "claim"},
        }[self.entity_type]
        entity = GraphEntity(
            tenant_id=source.tenant_id,
            entity_id=self.entity_id,
            entity_type=self.entity_type,
            attributes=attributes,
            project_id=source.project_id,
        )
        return (GraphExtractionDraft(
            entity=entity,
            name=source.display_name or source.source_id,
            confidence=0.91,
        ),)


class GraphStore:
    def __init__(self):
        self.entities = {}

    async def upsert_entity(self, entity, *, name, source_ref=None, allowed_project_ids=()):
        if entity.project_id is not None and entity.project_id not in allowed_project_ids:
            raise PermissionError("project access denied")
        self.entities[(entity.tenant_id, entity.entity_id)] = (entity, name, source_ref)

    async def upsert_relation(self, relation, *, allowed_project_ids=()):
        raise AssertionError("this fixture does not emit relations")

    async def delete_entity(self, entity_id, *, allowed_project_ids=()):
        return False


class Issue22C2203SourceDomainTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_four_canonical_domains_enter_the_same_review_gated_pipeline(self):
        schema = default_graph_schema("tenant-a")
        principal = GraphPrincipal(
            tenant_id="tenant-a",
            user_id="reviewer",
            project_ids=frozenset({"project-a"}),
            manage_project_ids=frozenset({"project-a"}),
            permissions=frozenset({"graph.extract"}),
        )
        candidates = InMemoryGraphCandidateRepository()
        graph = GraphStore()
        cases = (
            (GraphSourceDomain.KNOWLEDGE, "document", "document", "doc-1"),
            (GraphSourceDomain.MEMORY, "memory", "memory", "memory-1"),
            (GraphSourceDomain.BLACKBOARD, "entry", "blackboard_entry", "entry-1"),
            (GraphSourceDomain.FILE, "parsed_file", "document", "file-1"),
        )

        for domain, source_type, entity_type, source_id in cases:
            source = GraphSourceRecord(
                tenant_id="tenant-a",
                domain=domain,
                source_type=source_type,
                source_id=source_id,
                source_version=3,
                content=f"Parser-normalized source text for {source_id}.",
                project_id="project-a",
                display_name=source_id,
            )
            pipeline = GraphExtractionPipeline(
                schema=schema,
                extractor=DomainExtractor(entity_type, source_id),
                candidates=candidates,
                graph=graph,
            )
            proposed = await pipeline.propose(source=source, principal=principal)
            self.assertEqual(len(proposed), 1)
            candidate = proposed[0]
            self.assertEqual(candidate.status, GraphCandidateStatus.PENDING)
            self.assertEqual(candidate.source_reference.domain, domain)
            self.assertEqual(candidate.source_reference.source_type, source_type)
            self.assertEqual(candidate.source_reference.source_version, 3)
            self.assertFalse(hasattr(candidate, "content"), "source body must not be copied to candidate")
            self.assertNotIn((source.tenant_id, source_id), graph.entities)

            confirmed = await pipeline.confirm(
                principal=principal, candidate_id=candidate.candidate_id,
            )
            self.assertEqual(confirmed.status, GraphCandidateStatus.CONFIRMED)
            self.assertIn((source.tenant_id, source_id), graph.entities)

        self.assertEqual(len(graph.entities), 4)

    async def test_file_source_requires_parsed_file_mapping_and_never_accepts_raw_bytes(self):
        schema = default_graph_schema("tenant-a")
        self.assertIn(
            (GraphSourceDomain.FILE, "parsed_file", "document"),
            tuple((item.domain, item.source_type, item.entity_type) for item in schema.source_mappings),
        )
        principal = GraphPrincipal(
            tenant_id="tenant-a", user_id="reviewer",
            project_ids=frozenset({"project-a"}),
            manage_project_ids=frozenset({"project-a"}),
            permissions=frozenset({"graph.extract"}),
        )
        extractor = DomainExtractor("document", "file-raw")
        pipeline = GraphExtractionPipeline(
            schema=schema, extractor=extractor,
            candidates=InMemoryGraphCandidateRepository(), graph=GraphStore(),
        )
        raw_bytes_as_text = GraphSourceRecord(
            tenant_id="tenant-a", domain=GraphSourceDomain.FILE,
            source_type="file_asset", source_id="file-raw", source_version=1,
            content="binary\\x00payload", project_id="project-a",
        )
        with self.assertRaisesRegex(ValueError, "not mapped"):
            await pipeline.propose(source=raw_bytes_as_text, principal=principal)


if __name__ == "__main__":
    unittest.main()
