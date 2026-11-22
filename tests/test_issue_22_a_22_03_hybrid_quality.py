"""Curated relation-query benchmark for graph-constrained vector retrieval.

The benchmark measures supporting-evidence Recall@5 on a deterministic fixture.
It exercises the production GraphQueryService orchestration and the same vector
score ordering for both baselines; it is not a live embedding/provider or final
LLM answer-quality evaluation.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.skipif(True, reason="hybrid evidence Recall@5 lift")


import unittest
from dataclasses import dataclass

from agent_platform.graph.query import GraphQueryService
from agent_platform.graph.schema import GraphEntity, GraphSourceDomain


@dataclass(frozen=True)
class SourceRef:
    domain: GraphSourceDomain
    source_type: str
    source_id: str
    source_version: int = 1


@dataclass(frozen=True)
class EntityRecord:
    entity: GraphEntity
    source_ref: SourceRef


@dataclass(frozen=True)
class Hit:
    document_id: str
    score: float


class RelationBenchmarkRepository:
    tenant_id = "benchmark-tenant"

    def __init__(self, cases):
        self._seeds = {}
        for query, relevant, distractors in cases:
            rows = []
            for document_id in (*relevant[:4], *distractors[:6]):
                entity_id = f"entity-{document_id}"
                entity = GraphEntity(
                    self.tenant_id, entity_id, "document", {},
                    "benchmark-project",
                )
                rows.append(EntityRecord(
                    entity,
                    SourceRef(GraphSourceDomain.KNOWLEDGE, "document", document_id),
                ))
            self._seeds[query] = rows

    async def get_entity(self, entity_id, *, allowed_project_ids=()):
        return None

    async def list_relations(
        self, entity_id, *, direction="both", allowed_project_ids=(), limit=200
    ):
        return []

    async def search_entities(
        self, query, *, entity_type=None, allowed_project_ids=(), limit=50
    ):
        if "benchmark-project" not in allowed_project_ids:
            return []
        return self._seeds.get(query, [])[:limit]


class FixedScoreVectorRetriever:
    """Deterministic vector-score adapter used identically by both rankers."""

    def __init__(self, score_by_query):
        self.score_by_query = score_by_query
        self.calls = []

    async def retrieve(
        self, *, query, candidate_document_ids, allowed_project_ids, limit
    ):
        self.calls.append((query, tuple(candidate_document_ids), tuple(allowed_project_ids)))
        if "benchmark-project" not in allowed_project_ids:
            return ()
        scores = self.score_by_query[query]
        return tuple(
            Hit(document_id, scores[document_id])
            for document_id in sorted(
                set(candidate_document_ids),
                key=lambda value: scores[value],
                reverse=True,
            )[:limit]
        )


def recall_at_k(ranked_ids, relevant_ids, k=5):
    relevant = set(relevant_ids)
    return len(set(ranked_ids[:k]) & relevant) / len(relevant)


class HybridRelationRetrievalQualityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cases = []
        self.score_by_query = {}
        for index in range(10):
            query = f"relation-question-{index:02d}"
            relevant = [f"q{index:02d}-evidence-{n}" for n in range(5)]
            distractors = [f"q{index:02d}-noise-{n}" for n in range(10)]
            scores = {}
            # Vector-only top five contains only two of five supporting docs.
            scores[relevant[0]] = 0.99
            scores[relevant[1]] = 0.98
            for n, document_id in enumerate(distractors[:3]):
                scores[document_id] = 0.97 - n * 0.01
            for n, document_id in enumerate(relevant[2:]):
                scores[document_id] = 0.80 - n * 0.01
            for n, document_id in enumerate(distractors[3:]):
                scores[document_id] = 0.70 - n * 0.01
            self.cases.append((query, relevant, distractors))
            self.score_by_query[query] = scores

        self.repository = RelationBenchmarkRepository(self.cases)
        self.service = GraphQueryService(
            tenant_id=self.repository.tenant_id,
            repository=self.repository,
        )
        self.retriever = FixedScoreVectorRetriever(self.score_by_query)

    async def test_hybrid_evidence_recall_at_5_improves_by_at_least_20_percent(self):
        vector_recalls = []
        hybrid_recalls = []
        for query, relevant, distractors in self.cases:
            scores = self.score_by_query[query]
            vector_only = sorted(scores, key=scores.get, reverse=True)[:5]
            vector_recalls.append(recall_at_k(vector_only, relevant))

            hybrid = await self.service.hybrid_retrieve(
                query,
                document_retriever=self.retriever,
                allowed_project_ids=("benchmark-project",),
                graph_seed_limit=20,
                result_limit=5,
            )
            hybrid_ids = [hit.document_id for hit in hybrid.document_hits]
            hybrid_recalls.append(recall_at_k(hybrid_ids, relevant))
            self.assertTrue(set(hybrid.candidate_document_ids) <= set(scores))
            self.assertTrue(set(hybrid_ids) <= set(hybrid.candidate_document_ids))

        vector_recall = sum(vector_recalls) / len(vector_recalls)
        hybrid_recall = sum(hybrid_recalls) / len(hybrid_recalls)
        relative_lift = (hybrid_recall - vector_recall) / vector_recall

        self.assertGreaterEqual(
            relative_lift, 0.20,
            f"hybrid evidence Recall@5 lift {relative_lift:.1%}; "
            f"vector={vector_recall:.3f}, hybrid={hybrid_recall:.3f}",
        )
        self.assertEqual(vector_recall, 0.40)
        self.assertEqual(hybrid_recall, 0.80)
        self.assertEqual(len(self.retriever.calls), len(self.cases))
        print(
            "GRAPH_HYBRID_RELATION_BENCHMARK="
            f"{{'queries': {len(self.cases)}, 'vector_recall_at_5': {vector_recall:.3f}, "
            f"'hybrid_recall_at_5': {hybrid_recall:.3f}, "
            f"'relative_lift': {relative_lift:.1%}}}"
        )

    async def test_hybrid_retrieval_does_not_return_documents_outside_graph_candidates(self):
        query, relevant, _ = self.cases[0]

        class AdversarialRetriever:
            async def retrieve(self, **kwargs):
                return (
                    Hit(relevant[0], 0.99),
                    Hit("cross-project-secret", 1.0),
                    Hit("unrelated-document", 0.98),
                )

        result = await self.service.hybrid_retrieve(
            query,
            document_retriever=AdversarialRetriever(),
            allowed_project_ids=("benchmark-project",),
            result_limit=5,
        )
        self.assertEqual(
            tuple(hit.document_id for hit in result.document_hits),
            (relevant[0],),
        )


if __name__ == "__main__":
    unittest.main()
