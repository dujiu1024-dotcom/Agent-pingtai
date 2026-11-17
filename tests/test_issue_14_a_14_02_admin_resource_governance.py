from __future__ import annotations

import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_catalog import create_knowledge_catalog_router
from agent_platform.api.registry import create_registry_router
from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.identity.models import Principal
from agent_platform.knowledge.catalog import InMemoryKnowledgeCatalog
from agent_platform.knowledge.contracts import KnowledgePrincipal
from agent_platform.knowledge.repository import InMemoryKnowledgeRepository
from agent_platform.knowledge.service import KnowledgeService


class AdminResourceGovernanceAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry_service = RegistryService(InMemoryRegistryRepository())
        self.knowledge_catalog = InMemoryKnowledgeCatalog()
        self.knowledge = KnowledgeService(InMemoryKnowledgeRepository())

        async def registry_principal(request):
            return Principal(
                user_id=request.headers.get("x-user", "admin-a"),
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                session_id="session",
                email="admin@example.test",
                display_name="Admin",
            )

        async def registry_authorize(actor, kind, item_id, action):
            allowed = set(request_permissions.get((actor.user_id, action), ()))
            return kind in {"model_provider", "model_template", "tool"} and (
                "*" in allowed or kind in allowed
            )

        async def resolve_registry(_actor):
            return self.registry_service

        async def knowledge_principal(request):
            permissions = frozenset(request.headers.get("x-permissions", "").split())
            manage_projects = frozenset(request.headers.get("x-manage-projects", "").split())
            return KnowledgePrincipal(
                tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id=request.headers.get("x-user", "admin-a"),
                project_ids=frozenset({"project-a"}),
                manage_project_ids=manage_projects,
                permissions=permissions,
            )

        request_permissions: dict[tuple[str, str], tuple[str, ...]] = {
            ("admin-a", "read"): ("*",),
            ("admin-a", "write"): ("*",),
            ("admin-a", "publish"): ("*",),
            ("reader", "read"): ("model_provider", "tool"),
        }
        self.request_permissions = request_permissions
        app = FastAPI()
        app.include_router(create_registry_router(
            resolve_principal=registry_principal,
            resolve_service=resolve_registry,
            authorize=registry_authorize,
        ))
        app.include_router(create_knowledge_catalog_router(
            catalog=self.knowledge_catalog,
            knowledge=self.knowledge,
            resolve_principal=knowledge_principal,
        ))
        self.client = TestClient(app)

    def test_model_and_tool_admin_can_version_publish_list_and_enforce_permissions(self):
        for kind, item_id in (("model_provider", "provider-a"), ("model_template", "chat-template"), ("tool", "search-tool")):
            created = self.client.post(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions",
                json={"definition": {"name": item_id, "config": {"mode": "safe"}}},
            )
            self.assertEqual(created.status_code, 201, created.text)
            self.assertEqual(created.json()["version"], 1)
            published = self.client.post(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions/1/publish",
                json={},
            )
            self.assertEqual(published.status_code, 200, published.text)
            self.assertEqual(published.json()["version"], 2)
            self.assertEqual(published.json()["status"], "published")
            history = self.client.get(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions",
            )
            self.assertEqual([v["version"] for v in history.json()["items"]], [1, 2])
            self.assertEqual(history.json()["items"][1]["status"], "published")

            reader_history = self.client.get(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions",
                headers={"x-user": "reader"},
            )
            self.assertEqual(reader_history.status_code, 200)
            publish_denied = self.client.post(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions/1/publish",
                json={},
                headers={"x-user": "reader"},
            )
            self.assertEqual(publish_denied.status_code, 403)

            draft = self.client.post(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions",
                json={"definition": {"name": item_id, "config": {"mode": "strict"}}},
            )
            self.assertEqual(draft.status_code, 201)
            self.assertEqual(draft.json()["version"], 3)

            denied = self.client.post(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions",
                json={"definition": {"name": "unauthorized"}},
                headers={"x-user": "reader"},
            )
            self.assertEqual(denied.status_code, 403)

            other_tenant = self.client.get(
                f"/api/platform/v1/registry/{kind}/{item_id}/versions",
                headers={"x-tenant": "tenant-b"},
            )
            self.assertEqual(other_tenant.status_code, 200)
            self.assertEqual(other_tenant.json()["items"], [])

    def test_knowledge_collection_and_index_management_requires_admin_permission(self):
        denied = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Restricted"},
        )
        self.assertEqual(denied.status_code, 403)

        created = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Tenant knowledge"},
            headers={"x-permissions": "knowledge.manage"},
        )
        self.assertEqual(created.status_code, 201, created.text)
        collection_id = created.json()["collection_id"]

        index_denied = self.client.post(
            f"/api/platform/v1/knowledge/collections/{collection_id}/indexes",
            json={"index_name": "semantic", "embedding_model": "embed-v1", "dimensions": 768},
        )
        self.assertEqual(index_denied.status_code, 403)

        index = self.client.post(
            f"/api/platform/v1/knowledge/collections/{collection_id}/indexes",
            json={
                "index_name": "semantic-v1",
                "embedding_model": "embed-v1",
                "dimensions": 768,
                "schema_version": 2,
            },
            headers={"x-permissions": "knowledge.manage"},
        )
        self.assertEqual(index.status_code, 201, index.text)
        self.assertEqual(index.json()["schema_version"], 2)
        self.assertEqual(index.json()["embedding_model"], "embed-v1")

        hidden = self.client.get(
            "/api/platform/v1/knowledge/collections",
            headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(hidden.json()["items"], [])

    def test_project_manager_can_manage_only_visible_project_collection(self):
        denied = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Project A"},
        )
        self.assertEqual(denied.status_code, 403)

        allowed = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Project A", "project_id": "project-a"},
            headers={"x-manage-projects": "project-a"},
        )
        self.assertEqual(allowed.status_code, 201, allowed.text)
        self.assertEqual(allowed.json()["project_id"], "project-a")

        foreign = self.client.post(
            "/api/platform/v1/knowledge/collections",
            json={"name": "Project B", "project_id": "project-b"},
            headers={"x-permissions": "knowledge.manage"},
        )
        self.assertEqual(foreign.status_code, 404)


    def test_workbench_exposes_admin_registry_versions_and_knowledge_catalog_controls(self):
        root = Path(__file__).resolve().parents[1] / "agent_platform" / "web" / "static"
        html = (root / "index.html").read_text()
        script = (root / "app.js").read_text()
        self.assertIn('data-view="registry"', html)
        self.assertIn('data-view="governance"', html)
        for kind in ("model_provider", "model_template", "tool"):
            self.assertIn('"' + kind + '"', script)
        self.assertIn("data-reg-versions", script)
        self.assertIn("/versions", script)
        self.assertIn("/publish", script)
        self.assertIn('id="knowledge-collection-create"', script)
        self.assertIn('id="knowledge-index-create"', script)
        self.assertIn("/knowledge/collections", script)
        self.assertIn("/indexes", script)
        self.assertIn("X-CSRF-Token", script)



if __name__ == "__main__":
    unittest.main()
