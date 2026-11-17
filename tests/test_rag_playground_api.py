from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.rag_playground import create_rag_playground_router
from agent_platform.knowledge.contracts import KnowledgePrincipal, RetrievalHit
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.retrieval import RetrievalFilters, RetrievalStrategy


class RAGPlaygroundApiTests(unittest.TestCase):
    def setUp(self):
        self.principal = KnowledgePrincipal(
            tenant_id="tenant-a", user_id="user-a",
            permissions=frozenset({"knowledge.read"}),
        )
        self.hit = RetrievalHit(
            document_id="doc-a", document_version=2, chunk_id="chunk-a",
            text="Evidence for the answer.", source_uri="https://docs.example.test/source",
            title="Source document", score=0.91, page_number=3,
            start_offset=20, end_offset=44, citation_id="cite-a",
        )

        class Knowledge:
            def __init__(inner):
                inner.calls = []
            async def retrieve(inner, **kwargs):
                inner.calls.append(kwargs)
                return [self.hit]

        class Generator:
            async def generate(inner, *, query, context):
                return {"answer": "Grounded answer.", "citation_ids": [context[0]["citation_id"]]}

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

    def test_options_and_query_return_ranked_sources_and_grounded_answer(self):
        options = self.client.get("/api/platform/v1/rag-playground/options")
        self.assertEqual(options.status_code, 200)
        self.assertEqual(options.json()["default_embedding_model"], "embed-small")
        response = self.client.post("/api/platform/v1/rag-playground/query", json={
            "query": "What is the evidence?", "top_k": 4, "min_score": 0.7,
            "embedding_model": "embed-large", "rerank": True,
        })
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["answer"], "Grounded answer.")
        self.assertEqual(body["retrieved"][0]["chunk_id"], "chunk-a")
        self.assertEqual(body["retrieved"][0]["source_url"], "https://docs.example.test/source")
        self.assertEqual(body["citations"][0]["citation_id"], "cite-a")
        self.assertEqual(self.resolved, [("tenant-a", "embed-large", True)])
        call = self.knowledge.calls[0]
        self.assertEqual(call["limit"], 4)
        self.assertEqual(call["strategy"], RetrievalStrategy.HYBRID)
        self.assertIsInstance(call["filters"], RetrievalFilters)
        self.assertEqual(call["filters"].min_score, 0.7)

    def test_score_threshold_filters_hits_before_answer_generation(self):
        response = self.client.post("/api/platform/v1/rag-playground/query", json={
            "query": "What is the evidence?", "min_score": 0.95,
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["retrieved"], [])
        self.assertTrue(response.json()["abstained"])
        self.assertEqual(response.json()["reason"], "no_authorized_evidence")

    def test_rejects_unknown_embedding_and_invalid_query_parameters(self):
        unknown = self.client.post("/api/platform/v1/rag-playground/query", json={
            "query": "question", "embedding_model": "unconfigured",
        })
        self.assertEqual(unknown.status_code, 400)
        invalid_top_k = self.client.post("/api/platform/v1/rag-playground/query", json={
            "query": "question", "top_k": True,
        })
        self.assertEqual(invalid_top_k.status_code, 400)
        invalid_threshold = self.client.post("/api/platform/v1/rag-playground/query", json={
            "query": "question", "min_score": 1.5,
        })
        self.assertEqual(invalid_threshold.status_code, 400)


if __name__ == "__main__":
    unittest.main()
