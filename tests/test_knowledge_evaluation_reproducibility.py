"""Regression tests for reproducible RAG evaluation fingerprints and citation integrity."""
import unittest

from agent_platform.knowledge.contracts import KnowledgePrincipal, RetrievalHit
from agent_platform.knowledge.evaluation import (
    RAGAnswerEvalCase,
    evaluation_fingerprint,
    evaluate_answers,
)
from agent_platform.knowledge.rag import RAGAnswer


class ReproducibleRAGEvaluationTests(unittest.IsolatedAsyncioTestCase):
    def test_fingerprint_is_canonical_and_sensitive_to_dataset_changes(self):
        first = {"dataset_id": "smoke", "version": 1, "cases": [{"query": "alpha", "k": 3}]}
        reordered = {"cases": [{"k": 3, "query": "alpha"}], "version": 1, "dataset_id": "smoke"}
        changed = {"dataset_id": "smoke", "version": 1, "cases": [{"query": "beta", "k": 3}]}
        self.assertEqual(evaluation_fingerprint(first), evaluation_fingerprint(reordered))
        self.assertNotEqual(evaluation_fingerprint(first), evaluation_fingerprint(changed))
        with self.assertRaises(ValueError):
            evaluation_fingerprint({"not_finite": float("nan")})

    async def test_citation_must_match_retrieved_chunk_and_source(self):
        hit = RetrievalHit(
            document_id="doc-1", document_version=2, chunk_id="chunk-1",
            text="alpha evidence", source_uri="fixture://alpha", title="Alpha",
            score=1.0, citation_id="citation-1",
        )

        class FakeRAG:
            async def answer(self, **kwargs):
                return RAGAnswer(
                    answer="alpha evidence", retrieved=(hit,), abstained=False,
                    citations=({
                        "citation_id": "citation-1", "document_id": "doc-1",
                        "chunk_id": "chunk-1", "source_uri": "fixture://tampered",
                    },),
                )

        results = await evaluate_answers(
            FakeRAG(),
            [RAGAnswerEvalCase(
                case_id="citation-tamper", query="alpha",
                principal=KnowledgePrincipal(tenant_id="tenant-a", user_id="user-a"),
                expected_answer_terms=frozenset({"alpha"}),
                expected_citation_document_ids=frozenset({"doc-1"}),
            )],
        )
        self.assertFalse(results[0].citation_correctness)
        self.assertFalse(results[0].grounded)


if __name__ == "__main__":
    unittest.main()
