from __future__ import annotations

import unittest

from agent_platform.graph.extraction import (
    GraphEntityDraft,
    GraphExtractionDraft,
    GraphExtractionService,
    GraphRelationDraft,
    GraphSourceRecord,
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


class _Extractor:
    def __init__(self, draft):
        self.draft = draft

    async def extract(self, *, source, schema):
        return self.draft


class _Store:
    def __init__(self):
        self.batches = []

    async def append_candidates(self, candidates):
        self.batches.append(tuple(candidates))
        return len(candidates)


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


def _source():
    return GraphSourceRecord(
        tenant_id="tenant-a", domain=GraphSourceDomain.KNOWLEDGE,
        source_type="document", source_id="doc-1", source_version="3",
        source_ref="knowledge://tenant-a/doc-1@3", content="Ada works for Analytical",
        project_id="project-a",
    )


class GraphExtractionTests(unittest.IsolatedAsyncioTestCase):
    async def test_entity_relation_candidates_keep_source_and_evidence(self):
        draft = GraphExtractionDraft(
            entities=(
                GraphEntityDraft("person-1", "person", {"name": "Ada"}, 0.98,
                                 ("knowledge://tenant-a/doc-1@3",)),
                GraphEntityDraft("company-1", "company", {"name": "Analytical"}, 0.91,
                                 ("knowledge://tenant-a/doc-1@3",)),
            ),
            relations=(
                GraphRelationDraft("edge-1", "works_for", "person-1", "company-1",
                                   {}, 0.87, ("knowledge://tenant-a/doc-1@3",)),
            ),
        )
        store = _Store()
        service = GraphExtractionService(
            schema_provider=lambda tenant: _schema(),
            extractor=_Extractor(draft), candidate_store=store,
        )
        result = await service.extract_source(source=_source())
        self.assertEqual(len(result), 3)
        self.assertEqual(len(store.batches), 1)
        self.assertTrue(all(item.tenant_id == "tenant-a" for item in result))
        self.assertTrue(all(item.source_version == "3" for item in result))
        self.assertEqual(result[-1].payload["source_local_id"], "person-1")
        self.assertEqual(result[-1].status, "candidate")

    async def test_candidate_ids_are_deterministic_for_retries(self):
        draft = GraphExtractionDraft(
            entities=(GraphEntityDraft("person-1", "person", {"name": "Ada"}, 0.9,
                                       ("knowledge://tenant-a/doc-1@3",)),),
            relations=(),
        )
        async def run_once():
            store = _Store()
            service = GraphExtractionService(
                schema_provider=lambda tenant: _schema(),
                extractor=_Extractor(draft), candidate_store=store,
            )
            return await service.extract_source(source=_source())
        first = await run_once()
        second = await run_once()
        self.assertEqual(first[0].candidate_id, second[0].candidate_id)

    async def test_invalid_late_relation_prevents_any_candidate_write(self):
        draft = GraphExtractionDraft(
            entities=(GraphEntityDraft("person-1", "person", {"name": "Ada"}, 0.9,
                                       ("knowledge://tenant-a/doc-1@3",)),),
            relations=(GraphRelationDraft(
                "edge-1", "works_for", "person-1", "missing-company", {},
                0.9, ("knowledge://tenant-a/doc-1@3",),
            ),),
        )
        store = _Store()
        service = GraphExtractionService(
            schema_provider=lambda tenant: _schema(),
            extractor=_Extractor(draft), candidate_store=store,
        )
        with self.assertRaisesRegex(ValueError, "unknown local entity"):
            await service.extract_source(source=_source())
        self.assertEqual(store.batches, [])

    async def test_rejects_invalid_confidence_evidence_and_cross_tenant_schema(self):
        store = _Store()
        invalid = GraphExtractionDraft(
            entities=(GraphEntityDraft("person-1", "person", {"name": "Ada"}, 1.1,
                                       ("knowledge://tenant-a/doc-1@3",)),),
            relations=(),
        )
        service = GraphExtractionService(
            schema_provider=lambda tenant: _schema(),
            extractor=_Extractor(invalid), candidate_store=store,
        )
        with self.assertRaisesRegex(ValueError, "confidence"):
            await service.extract_source(source=_source())
        service = GraphExtractionService(
            schema_provider=lambda tenant: GraphSchema("tenant-b", (), (), ()),
            extractor=_Extractor(GraphExtractionDraft((), ())), candidate_store=store,
        )
        with self.assertRaisesRegex(ValueError, "schema tenant"):
            await service.extract_source(source=_source())
        self.assertEqual(store.batches, [])

    async def test_conflicting_candidates_are_marked_for_review(self):
        draft = GraphExtractionDraft(
            entities=(
                GraphEntityDraft("person-1", "person", {"name": "Ada"}, 0.9,
                                 ("knowledge://tenant-a/doc-1@3",), "person:ada"),
                GraphEntityDraft("person-2", "person", {"name": "Ada Lovelace"}, 0.8,
                                 ("knowledge://tenant-a/doc-1@3",), "person:ada"),
            ),
            relations=(),
        )
        store = _Store()
        service = GraphExtractionService(
            schema_provider=lambda tenant: _schema(),
            extractor=_Extractor(draft), candidate_store=store,
        )
        result = await service.extract_source(source=_source())
        self.assertEqual({item.status for item in result}, {"conflict"})


if __name__ == "__main__":
    unittest.main()
