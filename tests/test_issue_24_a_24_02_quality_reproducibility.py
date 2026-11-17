"""A-24-02 acceptance: identical RAG experiments produce identical metric reports."""
from __future__ import annotations

import copy
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.rag_playground import create_rag_playground_router
from agent_platform.knowledge.contracts import KnowledgePrincipal, RetrievalHit
from agent_platform.knowledge.rag import RAGService


DATASET = {
    "dataset_id": "a24-02-repeatability",
    "version": 3,
    "description": "Pinned deterministic RAG quality fixture",
    "documents": [
        {
            "key": "policy",
            "title": "Credential policy",
            "source_uri": "fixture://policy",
            "content": "Credential rotation is required every 90 days.",
        },
        {
            "key": "other",
            "title": "Other policy",
            "source_uri": "fixture://other",
            "content": "Unrelated retention guidance.",
        },
    ],
    "cases": [
        {
            "case_id": "rotation",
            "query": "credential rotation",
            "expected_document_keys": ["policy"],
            "expected_answer_terms": ["credential", "rotation", "90", "days"],
            "answer": "Credential rotation every 90 days",
            "expect_abstain": False,
        },
        {
            "case_id": "retention",
            "query": "retention",
            "expected_document_keys": ["other"],
            "expected_answer_terms": ["retention", "guidance"],
            "answer": "Retention guidance",
            "expect_abstain": False,
        },
    ],
}


class A2402QualityReproducibilityAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.principal = KnowledgePrincipal(
            tenant_id="tenant-a",
            user_id="user-a",
            permissions=frozenset({"knowledge.read"}),
        )

        class DeterministicKnowledge:
            async def retrieve(inner, **kwargs):
                query = kwargs["query"]
                if query == "credential rotation":
                    return [
                        RetrievalHit(
                            document_id="doc-policy",
                            document_version=3,
                            chunk_id="chunk-policy",
                            text="Credential rotation is required every 90 days.",
                            source_uri="fixture://policy",
                            title="Credential policy",
                            score=0.99,
                        )
                    ]
                return [
                    RetrievalHit(
                        document_id="doc-other",
                        document_version=3,
                        chunk_id="chunk-other",
                        text="Unrelated retention guidance.",
                        source_uri="fixture://other",
                        title="Other policy",
                        score=0.98,
                    )
                ]

        class DeterministicGenerator:
            async def generate(inner, *, query, context):
                return {"answer": "not used by retrieval evaluation", "citation_ids": []}

        rag = RAGService(
            knowledge=DeterministicKnowledge(),
            generator=DeterministicGenerator(),
        )
        self.resolutions = []

        async def resolve_principal(_request):
            return self.principal

        async def resolve_rag(principal, model, rerank):
            self.resolutions.append((principal.tenant_id, model, rerank))
            return rag

        app = FastAPI()
        app.include_router(create_rag_playground_router(
            resolve_principal=resolve_principal,
            resolve_rag=resolve_rag,
            embedding_models=("embed-small-v1", "embed-large-v2"),
        ))
        self.client = TestClient(app)

    def _evaluate(self, **overrides):
        payload = {
            "dataset": copy.deepcopy(DATASET),
            "top_k": 5,
            "min_score": 0.25,
            "embedding_model": "embed-small-v1",
            "rerank": False,
        }
        payload.update(overrides)
        return self.client.post(
            "/api/platform/v1/rag-playground/evaluate",
            json=payload,
        )

    def test_same_dataset_model_and_hyperparameters_repeat_identical_report(self):
        first = self._evaluate()
        second = self._evaluate()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.json(), second.json())
        report = first.json()
        self.assertEqual(report["dataset_id"], DATASET["dataset_id"])
        self.assertEqual(report["dataset_version"], DATASET["version"])
        self.assertEqual(report["evaluated_cases"], 2)
        self.assertEqual(report["ranking_scored_cases"], 2)
        self.assertEqual(report["hit_rate_at_k"], 1.0)
        self.assertEqual(report["mrr"], 1.0)
        self.assertEqual(report["ndcg_at_k"], 1.0)
        self.assertEqual(report["token_coverage"], 1.0)
        self.assertEqual(report["tokenizer"], "unicode-word-v1")
        self.assertEqual(len(report["dataset_fingerprint"]), 64)
        self.assertEqual(len(report["run_fingerprint"]), 64)
        self.assertEqual(
            self.resolutions,
            [
                ("tenant-a", "embed-small-v1", False),
                ("tenant-a", "embed-small-v1", False),
            ],
        )

    def test_run_fingerprint_changes_for_each_experiment_input(self):
        baseline = self._evaluate().json()["run_fingerprint"]
        variants = [
            self._evaluate(top_k=4).json()["run_fingerprint"],
            self._evaluate(min_score=0.5).json()["run_fingerprint"],
            self._evaluate(embedding_model="embed-large-v2").json()["run_fingerprint"],
            self._evaluate(rerank=True).json()["run_fingerprint"],
        ]
        changed_dataset = copy.deepcopy(DATASET)
        changed_dataset["cases"][0]["answer"] = "Credential rotation every 120 days"
        variants.append(self._evaluate(dataset=changed_dataset).json()["run_fingerprint"])

        self.assertTrue(all(value != baseline for value in variants))
        self.assertEqual(len(set(variants)), len(variants))

    def test_report_records_all_reproducibility_inputs(self):
        response = self._evaluate(
            top_k=4,
            min_score=0.4,
            embedding_model="embed-large-v2",
            rerank=True,
        )
        self.assertEqual(response.status_code, 200)
        report = response.json()
        self.assertEqual(report["top_k"], 4)
        self.assertEqual(report["min_score"], 0.4)
        self.assertEqual(report["embedding_model"], "embed-large-v2")
        self.assertTrue(report["rerank"])


if __name__ == "__main__":
    unittest.main()
