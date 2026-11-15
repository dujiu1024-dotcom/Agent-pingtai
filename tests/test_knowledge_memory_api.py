from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryPrincipal,
    MemoryService,
)


class KnowledgeMemoryApiTests(unittest.TestCase):
    def setUp(self):
        async def resolve_knowledge(request):
            return KnowledgePrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
                project_ids=frozenset({"project-a"}),
                roles=frozenset(request.headers.get("x-roles", "").split()),
            )

        async def resolve_memory(request):
            return MemoryPrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
                project_ids=frozenset({"project-a"}),
                agent_ids=frozenset({"agent-a"}),
                roles=frozenset(request.headers.get("x-roles", "").split()),
            )

        class CitationGenerator:
            async def generate(self, *, query, context):
                return {"answer": "Evidence-backed response.", "citation_ids": [context[0]["citation_id"]]}

        self.knowledge = KnowledgeService(InMemoryKnowledgeRepository())
        app = FastAPI()
        app.include_router(
            create_knowledge_memory_router(
                knowledge=self.knowledge,
                memory=MemoryService(InMemoryMemoryRepository()),
                resolve_knowledge_principal=resolve_knowledge,
                resolve_memory_principal=resolve_memory,
                rag=RAGService(knowledge=self.knowledge, generator=CitationGenerator()),
            )
        )
        self.client = TestClient(app)

    def test_private_document_upload_and_search_are_user_scoped(self):
        response = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={
                "title": "private note",
                "scope": "private",
                "content": "confidential project evidence",
            },
        )
        self.assertEqual(response.status_code, 200)
        document_id = response.json()["document_id"]
        overwrite = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={
                "document_id": document_id,
                "title": "attempted takeover",
                "scope": "private",
                "content": "overwritten private evidence",
            },
            headers={"x-user": "user-b"},
        )
        self.assertEqual(overwrite.status_code, 403)
        scope_change = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={
                "document_id": document_id,
                "title": "scope change",
                "scope": "tenant",
                "content": "scope escalation attempt",
            },
        )
        self.assertEqual(scope_change.status_code, 409)
        other = self.client.get(
            "/api/platform/v1/knowledge/search",
            params={"q": "confidential"},
            headers={"x-user": "user-b"},
        )
        owner = self.client.get(
            "/api/platform/v1/knowledge/search",
            params={"q": "confidential"},
        )
        self.assertEqual(other.json()["items"], [])
        self.assertEqual(len(owner.json()["items"]), 1)
        grounded = self.client.post(
            "/api/platform/v1/knowledge/answer",
            json={"query": "confidential"},
        )
        self.assertEqual(grounded.status_code, 200)
        self.assertFalse(grounded.json()["abstained"])
        self.assertEqual(len(grounded.json()["citations"]), 1)
        denied_answer = self.client.post(
            "/api/platform/v1/knowledge/answer",
            json={"query": "confidential"},
            headers={"x-user": "user-b"},
        )
        self.assertTrue(denied_answer.json()["abstained"])

    def test_project_document_write_requires_manage_permission(self):
        response = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={
                "title": "project note",
                "scope": "project",
                "project_id": "project-a",
                "content": "project evidence",
            },
            headers={"x-roles": "project.read"},
        )
        self.assertEqual(response.status_code, 403)

    def test_memory_requires_confirmation_and_respects_user_scope(self):
        proposed = self.client.post(
            "/api/platform/v1/memories/candidates",
            json={
                "scope": "user",
                "content": "prefers concise answers",
                "source_ref": "conversation:1",
                "confidence": 0.9,
            },
        )
        self.assertEqual(proposed.status_code, 200)
        memory_id = proposed.json()["memory_id"]
        self.assertEqual(
            self.client.get("/api/platform/v1/memories").json()["items"], []
        )
        confirmed = self.client.post(
            f"/api/platform/v1/memories/{memory_id}/confirm"
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(
            len(self.client.get("/api/platform/v1/memories").json()["items"]), 1
        )
        other = self.client.get(
            "/api/platform/v1/memories", headers={"x-user": "user-b"}
        )
        self.assertEqual(other.json()["items"], [])


if __name__ == "__main__":
    unittest.main()
