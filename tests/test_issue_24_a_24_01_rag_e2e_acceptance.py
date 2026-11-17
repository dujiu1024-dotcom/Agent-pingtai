from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_upload import create_knowledge_upload_router
from agent_platform.knowledge.citations import citation_context
from agent_platform.knowledge.chunking import clean_document_text
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.ingestion import ParserRegistry
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class DeterministicEmbedder:
    model_id = "acceptance-embedding"
    storage_key = "acceptance-embedding@v1"
    model_version = "v1"
    dimensions = 3

    async def embed_documents(self, texts):
        return [[1.0, float(index), 0.0] for index, _ in enumerate(texts)]

    async def embed_query(self, text):
        return [1.0, 0.0, 0.0]


class RagEndToEndAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_uploaded_source_is_chunked_embedded_retrieved_and_cited_to_original(self):
        repository = InMemoryKnowledgeRepository()
        embedder = DeterministicEmbedder()
        parser_registry = ParserRegistry()
        service = KnowledgeService(
            repository, parsers=parser_registry, embedder=embedder,
        )

        async def resolve_principal(_request):
            return KnowledgePrincipal(
                tenant_id="tenant-rag", user_id="researcher",
                permissions=frozenset({"knowledge.manage"}),
            )

        app = FastAPI()
        app.include_router(create_knowledge_upload_router(
            knowledge=service, resolve_principal=resolve_principal,
        ))
        client = TestClient(app)

        original = (
            "# Field Notes\n\n"
            "The quartz observatory recorded a rare aurora over the northern ridge. "
            "Researchers preserved the spectral measurements and calibration notes. "
            "The observation is indexed for later verification."
        )
        uploaded = client.post(
            "/api/platform/v1/knowledge/documents/upload?filename=field-notes.md",
            content=original.encode("utf-8"),
            headers={"content-type": "text/markdown"},
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        payload = uploaded.json()
        self.assertTrue(payload["embedded"])
        self.assertEqual(payload["embedding_model"], embedder.storage_key)
        self.assertGreaterEqual(payload["chunk_count"], 1)

        document_id = payload["document_id"]
        document = await repository.get_document("tenant-rag", document_id)
        self.assertIsNotNone(document)
        self.assertEqual(document.metadata["original_filename"], "field-notes.md")
        chunks = await repository.list_chunks("tenant-rag", document_id, 1)
        self.assertEqual(len(chunks), payload["chunk_count"])
        self.assertEqual(len(repository.embeddings), len(chunks))

        parsed_source = clean_document_text(
            parser_registry.parse(original.encode("utf-8"), "text/markdown")
        )
        for chunk in chunks:
            self.assertEqual(parsed_source[chunk.start_offset:chunk.end_offset], chunk.text)
            response_chunk = next(
                item for item in payload["chunks"] if item["chunk_id"] == chunk.chunk_id
            )
            self.assertEqual(response_chunk["start_offset"], chunk.start_offset)
            self.assertEqual(response_chunk["end_offset"], chunk.end_offset)
            self.assertEqual(response_chunk["source_uri"], document.source_uri)

        principal = KnowledgePrincipal(tenant_id="tenant-rag", user_id="researcher")
        hits = await service.retrieve(
            principal=principal, query="quartz observatory aurora", limit=5,
        )
        self.assertTrue(hits)
        hit = hits[0]
        self.assertEqual(hit.document_id, document_id)
        self.assertEqual(hit.document_version, payload["document_version"])
        self.assertIn("quartz observatory", hit.text.casefold())
        self.assertEqual(hit.source_uri, document.source_uri)

        citations = citation_context(hits)
        self.assertEqual(len(citations), len(hits))
        citation = next(item for item in citations if item["chunk_id"] == hit.chunk_id)
        self.assertEqual(citation["document_id"], document_id)
        self.assertEqual(citation["document_version"], 1)
        self.assertEqual(citation["source_uri"], document.source_uri)
        self.assertEqual(citation["source_locator"]["start_offset"], hit.start_offset)
        self.assertEqual(citation["source_locator"]["end_offset"], hit.end_offset)
        self.assertEqual(citation["text"], hit.text)
        self.assertEqual(
            parsed_source[hit.start_offset:hit.end_offset], hit.text,
        )

        other_tenant_hits = await service.retrieve(
            principal=KnowledgePrincipal(tenant_id="tenant-other", user_id="researcher"),
            query="quartz observatory aurora", limit=5,
        )
        self.assertEqual(other_tenant_hits, [])


if __name__ == "__main__":
    unittest.main()
