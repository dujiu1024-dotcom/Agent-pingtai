from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_upload import create_knowledge_upload_router
from agent_platform.api.rag_playground import create_rag_playground_router
from agent_platform.knowledge.connectors import ConnectorDocument, ConnectorPage
from agent_platform.knowledge.contracts import (
    Document,
    KnowledgePrincipal,
    KnowledgeScope,
)
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class AcceptanceEmbedder:
    model_id = "acceptance-embedding"
    storage_key = "acceptance-embedding@v1"
    model_version = "v1"
    dimensions = 3

    async def embed_query(self, text: str):
        return [1.0, 0.0, 0.0]

    async def embed_documents(self, texts):
        return [[1.0, 0.0, 0.0] for _ in texts]


class GroundedAcceptanceGenerator:
    """Deterministic generator: only cites source IDs supplied in its context."""

    async def generate(self, *, query: str, context):
        if not context:
            return {"answer": "", "citation_ids": []}
        return {
            "answer": f"Verified answer for: {query}",
            "citation_ids": [context[0]["citation_id"]],
        }


class FixtureKnowledgeConnector:
    connector_id = "acceptance-drive-connector"

    def __init__(self, document: Document, content: bytes):
        self.document = document
        self.content = content
        self.calls = []

    async def list_changed(self, *, tenant_id, cursor, changed_since=None, limit=100):
        self.calls.append((tenant_id, cursor, limit))
        if cursor is not None:
            return ConnectorPage(items=(), next_cursor=cursor, has_more=False)
        return ConnectorPage(
            items=(ConnectorDocument(document=self.document, content=self.content),),
            next_cursor="drive-cursor-v1",
            has_more=False,
        )

    async def revoke(self, *, tenant_id: str, credential_ref: str) -> None:
        return None


class KnowledgeEndToEndAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repository = InMemoryKnowledgeRepository()
        self.embedder = AcceptanceEmbedder()
        self.knowledge = KnowledgeService(
            self.repository,
            embedder=self.embedder,
            max_upload_bytes=4096,
        )
        self.generator = GroundedAcceptanceGenerator()
        self.rag = RAGService(knowledge=self.knowledge, generator=self.generator)

        async def resolve_principal(request):
            return KnowledgePrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
                project_ids=frozenset({"project-a"}),
                permissions=frozenset(request.headers.get("x-permissions", "").split()),
            )

        async def resolve_rag(principal, model, rerank):
            if model != self.embedder.storage_key or rerank:
                raise ValueError("acceptance resolver received unsupported model options")
            return self.rag

        app = FastAPI()
        app.include_router(create_knowledge_upload_router(
            knowledge=self.knowledge,
            resolve_principal=resolve_principal,
        ))
        app.include_router(create_rag_playground_router(
            resolve_principal=resolve_principal,
            resolve_rag=resolve_rag,
            embedding_models=(self.embedder.storage_key,),
        ))
        self.client = TestClient(app)

    async def test_upload_parse_embed_index_retrieve_and_cited_answer(self):
        source_text = (
            "Project ORBIT ingestion acceptance. "
            "The verified retention window is 47 days."
        )
        upload = self.client.post(
            "/api/platform/v1/knowledge/documents/upload",
            params={"filename": "orbit.md"},
            content=source_text.encode("utf-8"),
            headers={
                "content-type": "text/markdown",
                "x-tenant": "tenant-a",
                "x-user": "uploader",
                "x-permissions": "knowledge.manage",
            },
        )
        self.assertEqual(upload.status_code, 201, upload.text)
        uploaded = upload.json()
        self.assertTrue(uploaded["embedded"])
        self.assertGreater(uploaded["chunk_count"], 0)
        self.assertEqual(uploaded["embedding_model"], self.embedder.storage_key)
        self.assertTrue(self.repository.embeddings)

        query = self.client.post(
            "/api/platform/v1/rag-playground/query",
            json={"query": "ORBIT retention window 47 days", "top_k": 5},
            headers={"x-tenant": "tenant-a", "x-user": "uploader"},
        )
        self.assertEqual(query.status_code, 200, query.text)
        result = query.json()
        self.assertFalse(result["abstained"])
        self.assertIn("Verified answer", result["answer"])
        self.assertEqual(len(result["retrieved"]), 1)
        hit = result["retrieved"][0]
        self.assertEqual(hit["document_id"], uploaded["document_id"])
        self.assertEqual(hit["document_version"], uploaded["document_version"])
        self.assertEqual(hit["chunk_id"], uploaded["chunks"][0]["chunk_id"])
        self.assertEqual(hit["source_uri"], f"upload://{uploaded['document_id']}/orbit.md")
        self.assertGreater(hit["end_offset"], hit["start_offset"])
        self.assertEqual(len(result["citations"]), 1)
        citation = result["citations"][0]
        self.assertEqual(citation["citation_id"], hit["citation_id"])
        self.assertEqual(citation["document_id"], uploaded["document_id"])
        self.assertEqual(citation["document_version"], uploaded["document_version"])
        self.assertEqual(citation["chunk_id"], hit["chunk_id"])
        self.assertEqual(citation["source_locator"]["start_offset"], hit["start_offset"])

    async def test_connector_sync_parse_embed_index_retrieve_and_cited_answer(self):
        document = Document(
            tenant_id="tenant-a",
            document_id="drive:orbit-policy",
            title="ORBIT connector policy",
            source_uri="https://drive.example.test/orbit-policy",
            content_hash="pending",
            scope=KnowledgeScope.PRIVATE,
            owner_user_id="connector-owner",
            media_type="text/markdown",
            metadata={
                "_knowledge_acl": [{
                    "principal_type": "user",
                    "principal_id": "reader-a",
                    "permission": "read",
                }],
            },
        )
        connector = FixtureKnowledgeConnector(
            document,
            b"# ORBIT Connector\n\nThe connector retention code is 83-KAPPA.",
        )
        sync = await self.knowledge.sync_connector(
            connector=connector,
            tenant_id="tenant-a",
        )
        self.assertEqual(sync["documents"], 1)
        self.assertEqual(sync["cursor"], "drive-cursor-v1")
        self.assertEqual(
            self.repository.connector_cursors[
                ("tenant-a", connector.connector_id)
            ],
            "drive-cursor-v1",
        )
        self.assertTrue(self.repository.embeddings)

        # Connector ACL is enforced before retrieval; the mapped reader can query.
        query = self.client.post(
            "/api/platform/v1/rag-playground/query",
            json={"query": "ORBIT retention code 83-KAPPA", "top_k": 5},
            headers={"x-tenant": "tenant-a", "x-user": "reader-a"},
        )
        self.assertEqual(query.status_code, 200, query.text)
        result = query.json()
        self.assertFalse(result["abstained"])
        self.assertIn("Verified answer", result["answer"])
        self.assertEqual(len(result["retrieved"]), 1)
        hit = result["retrieved"][0]
        self.assertEqual(hit["document_id"], document.document_id)
        self.assertEqual(hit["source_uri"], document.source_uri)
        self.assertEqual(len(result["citations"]), 1)
        citation = result["citations"][0]
        self.assertEqual(citation["document_id"], document.document_id)
        self.assertEqual(citation["document_version"], 1)
        self.assertEqual(citation["chunk_id"], hit["chunk_id"])
        self.assertEqual(citation["source_uri"], document.source_uri)
        self.assertEqual(citation["source_locator"]["start_offset"], hit["start_offset"])

        # The same tenant cannot expose connector-private content to an unmapped user.
        denied = self.client.post(
            "/api/platform/v1/rag-playground/query",
            json={"query": "ORBIT retention code 83-KAPPA", "top_k": 5},
            headers={"x-tenant": "tenant-a", "x-user": "unmapped-user"},
        )
        self.assertEqual(denied.status_code, 200, denied.text)
        self.assertTrue(denied.json()["abstained"])
        self.assertEqual(denied.json()["reason"], "no_authorized_evidence")


if __name__ == "__main__":
    unittest.main()
