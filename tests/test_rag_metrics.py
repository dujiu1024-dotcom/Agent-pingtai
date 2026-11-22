from __future__ import annotations

import unittest

from agent_platform.knowledge.contracts import KnowledgePrincipal, RetrievalHit
from agent_platform.knowledge.evaluation_dataset import parse_evaluation_dataset
from agent_platform.knowledge.rag_metrics import evaluate_rag_dataset


class FakeKnowledge:
    def __init__(self, results):
        self.results = results
        self.calls = []

    async def retrieve(self, **kwargs):
        self.calls.append(kwargs)
        return list(self.results.get(kwargs["query"], ()))


def hit(document_id: str, source_uri: str, text: str) -> RetrievalHit:
    return RetrievalHit(
        document_id=document_id,
        document_version=1,
        chunk_id=f"{document_id}-chunk",
        text=text,
        source_uri=source_uri,
        title=document_id,
        score=0.9,
    )


class RAGMetricsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.principal = KnowledgePrincipal(
            tenant_id="tenant-a",
            user_id="user-a",
            permissions=frozenset({"knowledge.read"}),
        )
        self.dataset = parse_evaluation_dataset({
            "dataset_id": "quality-smoke",
            "version": 2,
            "description": "deterministic metric fixture",
            "documents": [
                {
                    "key": "policy",
                    "title": "Credential policy",
                    "source_uri": "fixture://policy",
                    "content": "Credential rotation is required every 90 days.",
                },
                {
                    "key": "other",
                    "title": "Other",
                    "source_uri": "fixture://other",
                    "content": "Unrelated text.",
                },
            ],
            "cases": [
                {
                    "case_id": "rotation",
                    "query": "rotation",
                    "expected_document_keys": ["policy"],
                    "expected_answer_terms": ["credential"],
                    "answer": "credential rotation every 90 days",
                    "expect_abstain": False,
                },
                {
                    "case_id": "abstain",
                    "query": "snacks",
                    "expected_document_keys": [],
                    "expected_answer_terms": [],
                    "answer": "",
                    "expect_abstain": True,
                },
            ],
        })

    async def test_batch_report_computes_hit_rate_mrr_ndcg_and_token_coverage(self):
        knowledge = FakeKnowledge({
            "rotation": [
                hit("other-id", "fixture://other", "Unrelated text."),
                hit("policy-id", "fixture://policy", "Credential rotation interval is 90 days."),
                hit("policy-id", "fixture://policy", "Credential rotation, 90 days."),
            ],
            "snacks": [],
        })
        report = await evaluate_rag_dataset(
            knowledge=knowledge,
            principal=self.principal,
            dataset=self.dataset,
            top_k=3,
            embedding_model="embed-v1",
            rerank=False,
            min_score=0.2,
        )
        self.assertEqual(report.evaluated_cases, 2)
        self.assertEqual(report.ranking_scored_cases, 1)
        self.assertEqual(report.token_scored_cases, 1)
        self.assertEqual(report.hit_rate_at_k, 1.0)
        self.assertEqual(report.mrr, 0.5)
        self.assertAlmostEqual(report.ndcg_at_k, 1 / 1.584962500721156)
        self.assertAlmostEqual(report.token_coverage, 0.8)
        self.assertIsNone(report.cases[1].hit_rate_at_k)
        self.assertEqual(len(report.run_fingerprint), 64)
        self.assertEqual(knowledge.calls[0]["principal"], self.principal)
        self.assertEqual(knowledge.calls[0]["limit"], 3)
        self.assertEqual(knowledge.calls[0]["filters"].min_score, 0.2)

    async def test_fingerprint_is_repeatable_and_bound_to_hyperparameters(self):
        knowledge = FakeKnowledge({"rotation": [hit("policy-id", "fixture://policy", "credential rotation every 90 days")], "snacks": []})
        kwargs = dict(
            knowledge=knowledge,
            principal=self.principal,
            dataset=self.dataset,
            top_k=5,
            embedding_model="embed-v1",
            rerank=False,
            min_score=0.0,
        )
        first = await evaluate_rag_dataset(**kwargs)
        second = await evaluate_rag_dataset(**kwargs)
        changed = await evaluate_rag_dataset(**{**kwargs, "top_k": 4})
        self.assertEqual(first.run_fingerprint, second.run_fingerprint)
        self.assertNotEqual(first.run_fingerprint, changed.run_fingerprint)

    async def test_rejects_invalid_top_k_and_threshold(self):
        knowledge = FakeKnowledge({})
        base = dict(
            knowledge=knowledge,
            principal=self.principal,
            dataset=self.dataset,
            embedding_model="embed-v1",
            rerank=False,
            min_score=0.0,
        )
        for values in ({"top_k": True}, {"top_k": 0}, {"top_k": 21}, {"top_k": 5, "min_score": 2.0}):
            merged = {**base, **values}
            with self.subTest(values=values), self.assertRaises(ValueError):
                await evaluate_rag_dataset(**merged)


if __name__ == "__main__":
    unittest.main()
