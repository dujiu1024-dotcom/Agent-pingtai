from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_memory import create_knowledge_memory_router
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.service import InMemoryMemoryRepository, MemoryPrincipal, MemoryService


class KnowledgeCollectionApiTests(unittest.TestCase):
    def setUp(self):
        async def resolve_knowledge(request):
            return KnowledgePrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
                project_ids=frozenset({"project-a"}),
                manage_project_ids=frozenset({"project-a"}),
                permissions=frozenset(request.headers.get("x-permissions", "").split()),
            )

        async def resolve_memory(request):
            return MemoryPrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
            )

        self.repository = InMemoryKnowledgeRepository()
        self.knowledge = KnowledgeService(self.repository)
        app = FastAPI()
        app.include_router(create_knowledge_memory_router(
            knowledge=self.knowledge,
            memory=MemoryService(InMemoryMemoryRepository()),
            resolve_knowledge_principal=resolve_knowledge,
            resolve_memory_principal=resolve_memory,
        ))
        self.client = TestClient(app)

    def test_collection_crud_catalog_and_default_index(self):
        denied = self.client.post("/api/platform/v1/knowledge/collections", json={"name": "engineering"})
        self.assertEqual(denied.status_code, 403)
        created = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "engineering", "description": "Engineering sources"},
            headers={"x-permissions": "knowledge.manage"},
        )
        self.assertEqual(created.status_code, 201)
        collection_id = created.json()["collection_id"]
        listed = self.client.get(
            "/api/platform/v1/knowledge/collections",
            headers={"x-permissions": "knowledge.manage"},
        )
        self.assertEqual(len(listed.json()["items"]), 1)
        indexes = self.client.get(
            f"/api/platform/v1/knowledge/collections/{collection_id}/indexes",
            headers={"x-permissions": "knowledge.manage"},
        )
        self.assertEqual(indexes.status_code, 200)
        self.assertEqual(indexes.json()["items"][0]["index_type"], "hybrid")

    def test_project_collection_visibility_and_tenant_isolation(self):
        created = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "project docs", "project_id": "project-a"},
        )
        self.assertEqual(created.status_code, 201)
        collection_id = created.json()["collection_id"]
        visible = self.client.get("/api/platform/v1/knowledge/collections")
        self.assertEqual(len(visible.json()["items"]), 1)
        foreign = self.client.get(
            "/api/platform/v1/knowledge/collections",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(foreign.json()["items"], [])
        indexes = self.client.get(
            f"/api/platform/v1/knowledge/collections/{collection_id}/indexes",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(indexes.status_code, 404)

    def test_document_is_bound_to_collection_and_project_boundary(self):
        collection = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "project docs", "project_id": "project-a"},
        ).json()
        wrong_project = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={"title": "mismatch", "scope": "project", "project_id": "project-b",
                  "collection_id": collection["collection_id"], "content": "evidence"},
        )
        self.assertEqual(wrong_project.status_code, 403)
        accepted = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={"title": "project source", "scope": "project", "project_id": "project-a",
                  "collection_id": collection["collection_id"], "content": "collection evidence"},
            headers={"x-roles": "knowledge.manage"},
        )
        self.assertEqual(accepted.status_code, 200)
        stored = next(iter(self.repository.documents.values()))
        self.assertEqual(stored.collection_id, collection["collection_id"])

    def test_collection_update_delete_and_document_guard(self):
        headers = {"x-permissions": "knowledge.manage"}
        created = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "engineering", "metadata": {"owner": "platform"}},
            headers=headers,
        )
        collection_id = created.json()["collection_id"]
        denied = self.client.patch(
            f"/api/platform/v1/knowledge/collections/{collection_id}",
            json={"name": "renamed"},
        )
        self.assertEqual(denied.status_code, 403)
        updated = self.client.patch(
            f"/api/platform/v1/knowledge/collections/{collection_id}",
            json={"name": "engineering-v2", "metadata": {"owner": "runtime"}},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["name"], "engineering-v2")
        self.assertEqual(updated.json()["metadata"], {"owner": "runtime"})
        immutable = self.client.patch(
            f"/api/platform/v1/knowledge/collections/{collection_id}",
            json={"project_id": "project-a"}, headers=headers,
        )
        self.assertEqual(immutable.status_code, 400)

        document = self.client.post(
            "/api/platform/v1/knowledge/documents",
            json={"title": "source", "scope": "tenant", "collection_id": collection_id,
                  "content": "attached source"},
            headers=headers,
        )
        self.assertEqual(document.status_code, 200)
        blocked = self.client.delete(
            f"/api/platform/v1/knowledge/collections/{collection_id}", headers=headers,
        )
        self.assertEqual(blocked.status_code, 409)
        removed_doc = self.client.delete(
            f"/api/platform/v1/knowledge/documents/{document.json()['document_id']}",
            headers=headers,
        )
        self.assertEqual(removed_doc.status_code, 204)
        deleted = self.client.delete(
            f"/api/platform/v1/knowledge/collections/{collection_id}", headers=headers,
        )
        self.assertEqual(deleted.status_code, 204)
        listed = self.client.get("/api/platform/v1/knowledge/collections", headers=headers)
        self.assertEqual(listed.json()["items"], [])

    def test_collection_lifecycle_is_tenant_scoped(self):
        created = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "tenant-a-only"}, headers={"x-permissions": "knowledge.manage"},
        )
        collection_id = created.json()["collection_id"]
        foreign = self.client.patch(
            f"/api/platform/v1/knowledge/collections/{collection_id}",
            json={"name": "stolen"},
            headers={"x-tenant": "tenant-b", "x-permissions": "knowledge.manage"},
        )
        self.assertEqual(foreign.status_code, 404)
        foreign_delete = self.client.delete(
            f"/api/platform/v1/knowledge/collections/{collection_id}",
            headers={"x-tenant": "tenant-b", "x-permissions": "knowledge.manage"},
        )
        self.assertEqual(foreign_delete.status_code, 404)
        self.assertEqual(
            self.client.get("/api/platform/v1/knowledge/collections",
                            headers={"x-permissions": "knowledge.manage"}).json()["items"][0]["collection_id"],
            collection_id,
        )

    def test_collection_name_and_index_contract_validation(self):
        from agent_platform.knowledge.contracts import KnowledgeCollection, KnowledgeIndex
        with self.assertRaises(ValueError):
            KnowledgeCollection(tenant_id="tenant-a", name=" ")
        with self.assertRaises(ValueError):
            KnowledgeIndex(tenant_id="tenant-a", collection_id="collection", index_type="unknown")


if __name__ == "__main__":
    unittest.main()
