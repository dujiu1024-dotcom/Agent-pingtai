from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.rag_playground import create_rag_playground_router
from agent_platform.knowledge.contracts import KnowledgePrincipal, RetrievalHit
from agent_platform.knowledge.rag import RAGService


DATASET = {
    "dataset_id": "api-quality",
    "version": 1,
    "description": "API metric fixture",
    "documents": [
        {
            "key": "policy",
            "title": "Policy",
            "source_uri": "fixture://policy",
            "content": "Credential rotation every 90 days.",
        }
    ],
    "cases": [
        {
            "case_id": "rotation",
            "query": "rotation",
            "expected_document_keys": ["policy"],
            "expected_answer_terms": ["credential"],
            "answer": "credential rotation every 90 days",
            "expect_abstain": False,
        }
    ],
}


class RAGMetricsApiTests(unittest.TestCase):
    def setUp(self):
        self.principal = KnowledgePrincipal(
            tenant_id="tenant-a",
            user_id="user-a",
            permissions=frozenset({"knowledge.read"}),
        )

        class Knowledge:
            def __init__(inner):
                inner.calls = []

            async def retrieve(inner, **kwargs):
                inner.calls.append(kwargs)
                return [RetrievalHit(
                    document_id="doc-policy",
                    document_version=1,
                    chunk_id="chunk-policy",
                    text="Credential rotation every 90 days.",
                    source_uri="fixture://policy",
                    title="Policy",
                    score=0.95,
                )]

        class Generator:
            async def generate(inner, *, query, context):
                return {"answer": "not used", "citation_ids": []}

        self.knowledge = Knowledge()
        self.rag = RAGService(knowledge=self.knowledge, generator=Generator())
        self.resolved = []

        async def principal(_request):
            return self.principal

        async def resolve_rag(principal, model, rerank):
            self.resolved.append((principal.tenant_id, model, rerank))
            return self.rag

        app = FastAPI()
        app.include_router(create_rag_playground_router(
            resolve_principal=principal,
            resolve_rag=resolve_rag,
            embedding_models=("embed-small", "embed-large"),
        ))
        self.client = TestClient(app)

    def test_evaluate_returns_batch_metrics_and_stable_fingerprint(self):
        response = self.client.post("/api/platform/v1/rag-playground/evaluate", json={
            "dataset": DATASET,
            "top_k": 4,
            "min_score": 0.7,
            "embedding_model": "embed-large",
            "rerank": True,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["dataset_id"], "api-quality")
        self.assertEqual(body["evaluated_cases"], 1)
        self.assertEqual(body["hit_rate_at_k"], 1.0)
        self.assertEqual(body["mrr"], 1.0)
        self.assertEqual(body["ndcg_at_k"], 1.0)
        self.assertEqual(body["token_coverage"], 1.0)
        self.assertEqual(body["tokenizer"], "unicode-word-v1")
        self.assertEqual(len(body["run_fingerprint"]), 64)
        self.assertEqual(self.resolved, [("tenant-a", "embed-large", True)])
        self.assertEqual(self.knowledge.calls[0]["principal"], self.principal)
        self.assertEqual(self.knowledge.calls[0]["limit"], 4)

    def test_evaluate_rejects_invalid_dataset_and_unconfigured_model(self):
        invalid = self.client.post("/api/platform/v1/rag-playground/evaluate", json={
            "dataset": {"dataset_id": "bad"},
        })
        self.assertEqual(invalid.status_code, 400)
        unknown_model = self.client.post("/api/platform/v1/rag-playground/evaluate", json={
            "dataset": DATASET,
            "embedding_model": "not-configured",
        })
        self.assertEqual(unknown_model.status_code, 400)

    def test_evaluate_does_not_accept_caller_supplied_identity(self):
        response = self.client.post("/api/platform/v1/rag-playground/evaluate", json={
            "dataset": DATASET,
            "tenant_id": "tenant-victim",
        })
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.knowledge.calls, [])


if __name__ == "__main__":
    unittest.main()
