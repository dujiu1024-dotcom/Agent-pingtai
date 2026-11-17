from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_catalog import create_knowledge_catalog_router
from agent_platform.knowledge.catalog import InMemoryKnowledgeCatalog
from agent_platform.knowledge.contracts import Document, KnowledgePrincipal, KnowledgeScope
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class KnowledgeCatalogApiTests(unittest.TestCase):
    def setUp(self):
        async def resolve(request):
            return KnowledgePrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "user-a"),
                project_ids=frozenset({"project-a"}),
                manage_project_ids=frozenset(request.headers.get("x-manage-projects", "").split()),
                permissions=frozenset(request.headers.get("x-permissions", "").split()),
            )

        self.documents = InMemoryKnowledgeRepository()
        self.knowledge = KnowledgeService(self.documents)
        self.catalog = InMemoryKnowledgeCatalog()
        app = FastAPI()
        app.include_router(create_knowledge_catalog_router(
            catalog=self.catalog,
            knowledge=self.knowledge,
            resolve_principal=resolve,
        ))
        self.client = TestClient(app)

    def test_create_list_and_get_collection_are_tenant_scoped(self):
        created = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Research"},
            headers={"x-permissions": "knowledge.manage"},
        )
        self.assertEqual(created.status_code, 201)
        collection_id = created.json()["collection_id"]
        self.assertEqual(
            self.client.get("/api/platform/v1/knowledge/collections").json()["items"][0]["name"],
            "Research",
        )
        self.assertEqual(self.client.get(
            f"/api/platform/v1/knowledge/collections/{collection_id}",
            headers={"x-tenant": "tenant-b"},
        ).status_code, 404)

    def test_project_collection_requires_manage_and_project_visibility(self):
        denied = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Project docs", "project_id": "project-a"},
        )
        self.assertEqual(denied.status_code, 403)
        allowed = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Project docs", "project_id": "project-a"},
            headers={"x-permissions": "knowledge.manage"},
        )
        self.assertEqual(allowed.status_code, 201)
        hidden = self.client.get(
            "/api/platform/v1/knowledge/collections",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(hidden.json()["items"], [])

    def test_document_membership_is_idempotent_and_index_is_validated(self):
        collection = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Docs"},
            headers={"x-permissions": "knowledge.manage"},
        ).json()["collection_id"]
        document = Document(
            tenant_id="tenant-a", title="Evidence", source_uri="upload:test",
            content_hash="sha256:test",
        )
        import asyncio
        asyncio.run(self.documents.save_document(document, []))
        url = f"/api/platform/v1/knowledge/collections/{collection}/documents"
        payload = {"document_id": document.document_id}
        self.assertEqual(self.client.post(url, json=payload, headers={
            "x-permissions": "knowledge.manage",
        }).status_code, 201)
        duplicate = self.client.post(url, json=payload, headers={
            "x-permissions": "knowledge.manage",
        })
        self.assertEqual(duplicate.status_code, 201)
        self.assertFalse(duplicate.json()["attached"])
        index_url = f"/api/platform/v1/knowledge/collections/{collection}/indexes"
        bad = self.client.post(index_url, json={
            "index_name": "semantic", "embedding_model": "model-v1", "dimensions": 0,
        }, headers={"x-permissions": "knowledge.manage"})
        self.assertEqual(bad.status_code, 400)
        bool_dimensions = self.client.post(index_url, json={
            "index_name": "bool-dimensions", "embedding_model": "model-v1", "dimensions": True,
        }, headers={"x-permissions": "knowledge.manage"})
        self.assertEqual(bool_dimensions.status_code, 400)
        bool_schema = self.client.post(index_url, json={
            "index_name": "bool-schema", "embedding_model": "model-v1",
            "dimensions": 1536, "schema_version": True,
        }, headers={"x-permissions": "knowledge.manage"})
        self.assertEqual(bool_schema.status_code, 400)
        good = self.client.post(index_url, json={
            "index_name": "semantic", "embedding_model": "model-v1", "dimensions": 1536,
        }, headers={"x-permissions": "knowledge.manage"})
        self.assertEqual(good.status_code, 201)
        self.assertEqual(good.json()["status"], "pending")



    def test_collection_rejects_private_and_cross_project_documents(self):
        import asyncio

        project_collection = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Project A", "project_id": "project-a"},
            headers={"x-permissions": "knowledge.manage"},
        ).json()["collection_id"]
        private_doc = Document(
            tenant_id="tenant-a", title="Private", source_uri="upload:private",
            content_hash="sha256:private", scope=KnowledgeScope.PRIVATE,
            owner_user_id="user-a",
        )
        project_b_doc = Document(
            tenant_id="tenant-a", title="Project B", source_uri="upload:project-b",
            content_hash="sha256:project-b", scope=KnowledgeScope.PROJECT,
            project_id="project-b",
        )
        asyncio.run(self.documents.save_document(private_doc, []))
        asyncio.run(self.documents.save_document(project_b_doc, []))
        url = f"/api/platform/v1/knowledge/collections/{project_collection}/documents"
        for document in (private_doc, project_b_doc):
            response = self.client.post(
                url, json={"document_id": document.document_id},
                headers={"x-permissions": "knowledge.manage"},
            )
            self.assertEqual(response.status_code, 404)

    def test_index_rejects_boolean_dimensions_and_schema_version(self):
        collection = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Typed indexes"},
            headers={"x-permissions": "knowledge.manage"},
        ).json()["collection_id"]
        url = f"/api/platform/v1/knowledge/collections/{collection}/indexes"
        for payload in (
            {"index_name": "bad-dim", "embedding_model": "model-v1", "dimensions": True},
            {"index_name": "bad-schema", "embedding_model": "model-v1", "dimensions": 1536, "schema_version": True},
        ):
            response = self.client.post(
                url, json=payload, headers={"x-permissions": "knowledge.manage"},
            )
            self.assertEqual(response.status_code, 400, response.text)

if __name__ == "__main__":
    unittest.main()
