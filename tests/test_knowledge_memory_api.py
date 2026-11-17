from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.extraction import MemoryDraft, MemoryExtractionService
from agent_platform.memory.service import (
    InMemoryMemoryRepository,
    MemoryPrincipal,
    MemoryScope,
    MemoryService,
)


class KnowledgeMemoryApiTests(unittest.TestCase):
    def setUp(self):
        async def resolve_knowledge(request):
            return KnowledgePrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
                project_ids=frozenset({"project-a"}),
                permissions=frozenset(request.headers.get("x-roles", "").split()),
            )

        async def resolve_memory(request):
            return MemoryPrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
                project_ids=frozenset({"project-a"}),
                agent_ids=frozenset({"agent-a"}),
                permissions=frozenset(request.headers.get("x-permissions", "").split()),
            )

        class CitationGenerator:
            async def generate(self, *, query, context):
                return {"answer": "Evidence-backed response.", "citation_ids": [context[0]["citation_id"]]}

        class Extractor:
            async def extract(self, *, turn):
                return [MemoryDraft(
                    scope=MemoryScope.USER,
                    scope_id=turn.user_id,
                    content="prefers concise explanations",
                    confidence=0.88,
                )]

        self.knowledge = KnowledgeService(InMemoryKnowledgeRepository())
        self.memory = MemoryService(InMemoryMemoryRepository())
        extraction = MemoryExtractionService(memory=self.memory, extractor=Extractor())
        app = FastAPI()
        app.include_router(
            create_knowledge_memory_router(
                knowledge=self.knowledge,
                memory=self.memory,
                resolve_knowledge_principal=resolve_knowledge,
                resolve_memory_principal=resolve_memory,
                rag=RAGService(knowledge=self.knowledge, generator=CitationGenerator()),
                memory_extraction=extraction,
            )
        )
        self.client = TestClient(app)

    def test_retrieval_and_answer_evaluation_api_returns_metrics(self):
        response = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={"title": "evaluation doc", "scope": "private",
                  "content": "The evaluation answer contains alpha evidence."},
        )
        self.assertEqual(response.status_code, 200)
        document_id = response.json()["document_id"]
        payload = {"dataset_id": "api-smoke", "dataset_version": 1, "k": 3, "cases": [{
            "case_id": "alpha-case", "query": "alpha evidence",
            "expected_document_ids": [document_id],
            "expected_answer_terms": ["Evidence-backed"],
            "expected_citation_document_ids": [document_id],
        }]}
        denied = self.client.post("/api/platform/v1/knowledge/evaluate", json=payload)
        self.assertEqual(denied.status_code, 403)
        result = self.client.post(
            "/api/platform/v1/knowledge/evaluate", json=payload,
            headers={"x-roles": "knowledge.manage"},
        )
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["retrieval_summary"]["case_count"], 1)
        self.assertEqual(result.json()["dataset_id"], "api-smoke")
        self.assertEqual(result.json()["dataset_version"], 1)
        self.assertEqual(len(result.json()["evaluation_fingerprint"]), 64)
        self.assertEqual(result.json()["answer_summary"]["grounded_cases"], 1)
        self.assertEqual(result.json()["answer_summary"]["answer_accuracy"], 1.0)
        self.assertEqual(result.json()["answer_summary"]["citation_correctness_rate"], 1.0)

    def test_chunk_preview_and_manual_edit_are_acl_scoped(self):
        created = self.client.post("/api/platform/v1/knowledge/documents", json={"title":"chunk source", "scope":"private", "content":"alpha beta gamma delta"})
        document_id = created.json()["document_id"]
        preview = self.client.get(f"/api/platform/v1/knowledge/documents/{document_id}/chunks")
        self.assertEqual(preview.status_code, 200)
        chunk = preview.json()["items"][0]
        self.assertEqual(chunk["token_count"], 4)
        denied = self.client.patch(f"/api/platform/v1/knowledge/documents/{document_id}/chunks/{chunk['chunk_id']}", json={"text":"tampered"}, headers={"x-user":"user-b"})
        self.assertEqual(denied.status_code, 403)
        updated = self.client.patch(f"/api/platform/v1/knowledge/documents/{document_id}/chunks/{chunk['chunk_id']}", json={"text":"edited chunk body"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["embedding_status"], "stale")
        refreshed = self.client.get(f"/api/platform/v1/knowledge/documents/{document_id}/chunks")
        self.assertEqual(refreshed.json()["items"][0]["text"], "edited chunk body")

    def test_chunk_acl_is_applied_to_search_and_chunk_preview_api(self):
        created = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={"title": "fragment ACL", "scope": "tenant", "content": "restricted fragment marker"},
            headers={"x-roles": "knowledge.manage"},
        )
        self.assertEqual(created.status_code, 200)
        document_id = created.json()["document_id"]
        chunks = self.client.get(
            f"/api/platform/v1/knowledge/documents/{document_id}/chunks",
            headers={"x-roles": "knowledge.manage"},
        ).json()["items"]
        chunk_id = chunks[0]["chunk_id"]
        granted = self.client.post(
            f"/api/platform/v1/knowledge/documents/{document_id}/chunks/{chunk_id}/acl",
            json={"principal_type": "user", "principal_id": "user-b", "permission": "read"},
            headers={"x-roles": "knowledge.manage"},
        )
        self.assertEqual(granted.status_code, 201)
        denied_preview = self.client.get(
            f"/api/platform/v1/knowledge/documents/{document_id}/chunks",
            headers={"x-user": "user-a", "x-roles": "knowledge.manage"},
        )
        self.assertEqual(denied_preview.json()["items"], [])
        allowed_preview = self.client.get(
            f"/api/platform/v1/knowledge/documents/{document_id}/chunks",
            headers={"x-user": "user-b"},
        )
        self.assertEqual([item["chunk_id"] for item in allowed_preview.json()["items"]], [chunk_id])
        denied_search = self.client.get(
            "/api/platform/v1/knowledge/search", params={"q": "restricted"},
            headers={"x-user": "user-a"},
        )
        allowed_search = self.client.get(
            "/api/platform/v1/knowledge/search", params={"q": "restricted"},
            headers={"x-user": "user-b"},
        )
        self.assertEqual(denied_search.json()["items"], [])
        self.assertEqual(len(allowed_search.json()["items"]), 1)
        self.assertIn("source_locator", allowed_search.json()["items"][0])
        revoked = self.client.delete(
            f"/api/platform/v1/knowledge/documents/{document_id}/chunks/{chunk_id}/acl/user/user-b",
            headers={"x-roles": "knowledge.manage"},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertEqual(self.client.get(
            "/api/platform/v1/knowledge/search", params={"q": "restricted"},
            headers={"x-user": "user-b"},
        ).json()["items"], [])

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

    def test_project_memory_candidate_requires_manage_permission(self):
        response = self.client.post(
            "/api/platform/v1/memories/candidates",
            json={
                "scope": "project",
                "scope_id": "project-a",
                "content": "read-only user should not write",
                "source_ref": "project:1",
            },
        )
        self.assertEqual(response.status_code, 403)

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

    def test_extraction_creates_candidates_without_auto_activation(self):
        response = self.client.post(
            "/api/platform/v1/memories/extract",
            json={
                "source_ref": "conversation:turn-42",
                "user_message": "I prefer concise explanations.",
                "assistant_message": "I will keep responses concise.",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["status"], "candidate")
        self.assertEqual(
            self.client.get("/api/platform/v1/memories").json()["items"], []
        )
        memory_id = response.json()["items"][0]["memory_id"]
        confirmed = self.client.post(
            f"/api/platform/v1/memories/{memory_id}/confirm"
        )
        self.assertEqual(confirmed.status_code, 200)
        self.assertEqual(
            len(self.client.get("/api/platform/v1/memories").json()["items"]), 1
        )

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


    def test_memory_correction_and_export_api_are_scoped(self):
        proposed = self.client.post(
            "/api/platform/v1/memories/candidates",
            json={"scope":"user","content":"old preference","source_ref":"conversation:1","confidence":0.8},
        )
        memory_id = proposed.json()["memory_id"]
        self.assertEqual(self.client.post(f"/api/platform/v1/memories/{memory_id}/confirm").status_code, 200)
        corrected = self.client.patch(
            f"/api/platform/v1/memories/{memory_id}",
            json={"content":"updated preference","source_ref":"conversation:2","confidence":0.95},
        )
        self.assertEqual(corrected.status_code, 200)
        self.assertEqual(corrected.json()["version"], 3)
        exported = self.client.get("/api/platform/v1/memories/export")
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.json()["items"][0]["content"], "updated preference")
        self.assertEqual(exported.json()["items"][0]["source_ref"], "conversation:2")
        other = self.client.get("/api/platform/v1/memories/export", headers={"x-user":"user-b"})
        self.assertEqual(other.json()["items"], [])
        denied = self.client.patch(
            f"/api/platform/v1/memories/{memory_id}",
            json={"content":"takeover","source_ref":"conversation:3"},
            headers={"x-user":"user-b"},
        )
        self.assertEqual(denied.status_code, 403)

    def test_memory_correction_rejects_invalid_payload(self):
        proposed = self.client.post(
            "/api/platform/v1/memories/candidates",
            json={"scope":"user","content":"old preference","source_ref":"conversation:1"},
        )
        memory_id = proposed.json()["memory_id"]
        self.client.post(f"/api/platform/v1/memories/{memory_id}/confirm")
        for payload in (
            {"content":"","source_ref":"conversation:2"},
            {"content":"new","source_ref":""},
            {"content":"new","source_ref":"conversation:2","confidence":True},
            {"content":"new","source_ref":"conversation:2","confidence":1.1},
        ):
            response = self.client.patch(f"/api/platform/v1/memories/{memory_id}",json=payload)
            self.assertEqual(response.status_code,400)



if __name__ == "__main__":
    unittest.main()
