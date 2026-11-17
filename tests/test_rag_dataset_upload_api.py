from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import InMemoryMemoryRepository, MemoryPrincipal, MemoryService


class FakeEmbedder:
    model_id = "test-embedding-v1"
    dimensions = 3

    async def embed_documents(self, texts):
        return [[float(len(text)), 1.0, 0.0] for text in texts]

    async def embed_query(self, text):
        return [float(len(text)), 1.0, 0.0]


class RagDatasetUploadApiTests(unittest.TestCase):
    def make_client(self, *, embedder=None):
        async def resolve_knowledge(request):
            return KnowledgePrincipal(
                tenant_id="tenant-a", user_id="user-a",
                permissions=frozenset({"knowledge.manage"}),
                manage_project_ids=frozenset({"project-a"}),
            )

        async def resolve_memory(request):
            return MemoryPrincipal(
                tenant_id="tenant-a", user_id="user-a",
                project_ids=frozenset({"project-a"}),
                agent_ids=frozenset(), permissions=frozenset({"memory.manage"}),
            )

        repository = InMemoryKnowledgeRepository()
        knowledge = KnowledgeService(repository, embedder=embedder)
        app = FastAPI()
        app.include_router(create_knowledge_memory_router(
            knowledge=knowledge,
            memory=MemoryService(InMemoryMemoryRepository()),
            resolve_knowledge_principal=resolve_knowledge,
            resolve_memory_principal=resolve_memory,
        ))
        return TestClient(app), repository

    def test_upload_options_expose_parser_types_and_chunk_defaults(self):
        client, _ = self.make_client(embedder=FakeEmbedder())
        response = client.get("/api/platform/v1/knowledge/upload-options")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("text/markdown", payload["supported_media_types"])
        self.assertIn("text/html", payload["supported_media_types"])
        self.assertIn("application/pdf", payload["supported_media_types"])
        self.assertEqual(payload["default_max_chars"], 1200)
        self.assertEqual(payload["default_overlap"], 160)
        self.assertTrue(payload["embedding_configured"])
        self.assertEqual(payload["embedding_model"], "test-embedding-v1")

    def test_upload_applies_chunk_parameters_and_persists_embeddings(self):
        client, repository = self.make_client(embedder=FakeEmbedder())
        content = ("Alpha beta gamma delta. " * 40).encode("utf-8")
        response = client.post(
            "/api/platform/v1/knowledge/upload",
            params={"title": "RAG dataset", "max_chars": 120, "overlap": 20},
            content=content,
            headers={"content-type": "text/markdown"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        payload = response.json()
        self.assertGreater(payload["chunk_count"], 1)
        self.assertEqual(payload["chunking"], {"max_chars": 120, "overlap": 20})
        self.assertEqual(payload["index_status"], "indexed")
        self.assertEqual(payload["embedding_model"], "test-embedding-v1")
        self.assertEqual(len(repository.embeddings), payload["chunk_count"])

    def test_upload_reports_pending_embedding_when_host_has_no_embedder(self):
        client, repository = self.make_client()
        response = client.post(
            "/api/platform/v1/knowledge/upload",
            params={"title": "Pending dataset"},
            content=b"plain text document for embedding later",
            headers={"content-type": "text/plain"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["index_status"], "pending_embedding")
        self.assertEqual(repository.embeddings, {})

    def test_invalid_chunk_parameters_and_unsupported_media_type_are_rejected(self):
        client, repository = self.make_client(embedder=FakeEmbedder())
        invalid_chunking = client.post(
            "/api/platform/v1/knowledge/upload",
            params={"title": "Invalid", "max_chars": 100, "overlap": 100},
            content=b"not indexed",
            headers={"content-type": "text/plain"},
        )
        self.assertEqual(invalid_chunking.status_code, 400)
        unsupported = client.post(
            "/api/platform/v1/knowledge/upload",
            params={"title": "Unsupported"},
            content=b"binary",
            headers={"content-type": "application/octet-stream"},
        )
        self.assertEqual(unsupported.status_code, 415)
        self.assertEqual(repository.documents, {})


if __name__ == "__main__":
    unittest.main()
