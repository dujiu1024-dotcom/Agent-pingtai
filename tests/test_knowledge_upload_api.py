from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_upload import create_knowledge_upload_router
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.ingestion import ParserRegistry
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class FakeEmbedder:
    model_id = "test-embedding"
    storage_key = "test-embedding@v1"
    model_version = "v1"
    dimensions = 3

    def __init__(self):
        self.calls: list[list[str]] = []

    async def embed_query(self, text: str):
        return [1.0, 0.0, 0.0]

    async def embed_documents(self, texts):
        self.calls.append(list(texts))
        return [[float(index + 1), 0.0, 0.0] for index, _ in enumerate(texts)]


class PDFTestParserRegistry(ParserRegistry):
    def parse(self, content: bytes, media_type: str) -> str:
        if media_type == "application/pdf":
            if not content.startswith(b"%PDF-"):
                raise ValueError("invalid PDF")
            return "Extracted PDF page content"
        return super().parse(content, media_type)


class KnowledgeUploadApiTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryKnowledgeRepository()
        self.embedder = FakeEmbedder()
        self.knowledge = KnowledgeService(
            self.repository, parsers=PDFTestParserRegistry(), embedder=self.embedder,
            max_upload_bytes=2048,
        )

        async def resolve_principal(request):
            return KnowledgePrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
                project_ids=frozenset({"project-a"}),
                manage_project_ids=frozenset(request.headers.get("x-manage-projects", "").split()),
                permissions=frozenset(request.headers.get("x-permissions", "").split()),
            )

        app = FastAPI()
        app.include_router(create_knowledge_upload_router(
            knowledge=self.knowledge, resolve_principal=resolve_principal,
        ))
        self.client = TestClient(app)

    def upload(self, content: bytes, filename: str, media_type: str, **params):
        return self.client.post(
            "/api/platform/v1/knowledge/documents/upload",
            params={"filename": filename, **params},
            content=content,
            headers={"content-type": media_type, "x-permissions": "knowledge.manage"},
        )

    def test_markdown_upload_chunks_embeds_and_persists_with_provenance(self):
        response = self.upload(
            b"# Research\n\nA repeatable knowledge ingestion pipeline.",
            "research.md", "text/markdown",
        )
        self.assertEqual(response.status_code, 201, response.text)
        payload = response.json()
        self.assertEqual(payload["media_type"], "text/markdown")
        self.assertGreaterEqual(payload["chunk_count"], 1)
        self.assertTrue(payload["embedded"])
        self.assertEqual(payload["embedding_model"], "test-embedding@v1")
        document = self.repository.documents[("tenant-a", payload["document_id"], 1)]
        chunks = self.repository.chunks[("tenant-a", payload["document_id"], 1)]
        self.assertEqual(document.metadata["original_filename"], "research.md")
        self.assertEqual(len(self.repository.embeddings), len(chunks))
        self.assertEqual(payload["chunks"][0]["source_uri"], document.source_uri)
        self.assertEqual(payload["chunks"][0]["start_offset"], chunks[0].start_offset)

    def test_html_upload_uses_shared_parser_and_excludes_script_content(self):
        response = self.upload(
            b"<html><body><h1>Title</h1><p>Visible text</p><script>secret()</script></body></html>",
            "page.html", "text/html",
        )
        self.assertEqual(response.status_code, 201, response.text)
        chunk = self.repository.chunks[("tenant-a", response.json()["document_id"], 1)][0]
        self.assertIn("Visible text", chunk.text)
        self.assertNotIn("secret", chunk.text)

    def test_pdf_upload_uses_registered_pdf_parser_and_embeds(self):
        response = self.upload(b"%PDF-1.7 test", "report.pdf", "application/pdf")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["media_type"], "application/pdf")
        self.assertTrue(response.json()["embedded"])

    def test_tenant_scope_requires_manage_permission(self):
        response = self.client.post(
            "/api/platform/v1/knowledge/documents/upload?filename=note.md",
            content=b"hello", headers={"content-type": "text/markdown"},
        )
        self.assertEqual(response.status_code, 403)

    def test_project_upload_requires_project_manage_and_matching_scope(self):
        denied = self.client.post(
            "/api/platform/v1/knowledge/documents/upload?filename=note.md&scope=project&project_id=project-a",
            content=b"hello",
            headers={"content-type": "text/markdown"},
        )
        self.assertEqual(denied.status_code, 403)
        allowed = self.client.post(
            "/api/platform/v1/knowledge/documents/upload?filename=note.md&scope=project&project_id=project-a",
            content=b"hello",
            headers={"content-type": "text/markdown", "x-manage-projects": "project-a"},
        )
        self.assertEqual(allowed.status_code, 201, allowed.text)

    def test_upload_rejects_type_mismatch_empty_and_oversized_content(self):
        mismatch = self.upload(b"hello", "note.md", "text/html")
        self.assertEqual(mismatch.status_code, 415)
        empty = self.upload(b"", "empty.md", "text/markdown")
        self.assertEqual(empty.status_code, 400)
        oversized = self.upload(b"x" * 2049, "large.md", "text/markdown")
        self.assertEqual(oversized.status_code, 413)

    def test_upload_requires_embedding_provider_instead_of_silent_partial_index(self):
        service = KnowledgeService(InMemoryKnowledgeRepository())
        app = FastAPI()

        async def principal(_request):
            return KnowledgePrincipal(
                tenant_id="tenant-a", user_id="user-a",
                permissions=frozenset({"knowledge.manage"}),
            )

        app.include_router(create_knowledge_upload_router(
            knowledge=service, resolve_principal=principal,
        ))
        response = TestClient(app).post(
            "/api/platform/v1/knowledge/documents/upload?filename=note.md",
            content=b"hello", headers={"content-type": "text/markdown"},
        )
        self.assertEqual(response.status_code, 503)


if __name__ == "__main__":
    unittest.main()
