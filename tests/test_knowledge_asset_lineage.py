from __future__ import annotations

import unittest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_assets import create_knowledge_asset_router
from agent_platform.knowledge.asset_lineage import (
    AssetKind, InMemoryKnowledgeAssetRepository, KnowledgeAsset,
    KnowledgeLineageEdge, LineageRelation,
)
from agent_platform.knowledge.contracts import KnowledgePrincipal


class KnowledgeAssetLineageTests(unittest.IsolatedAsyncioTestCase):
    async def test_registration_and_tenant_isolation(self):
        repo = InMemoryKnowledgeAssetRepository()
        asset = KnowledgeAsset(
            tenant_id="t1", name="HR handbook", kind=AssetKind.DOCUMENT,
            owner_id="u1", owner_department="People", business_domain="HR",
            classification="internal", source_system="sharepoint", external_id="doc-1",
        )
        self.assertTrue(await repo.register_asset(asset))
        self.assertFalse(await repo.register_asset(KnowledgeAsset(
            tenant_id="t1", name="Duplicate", kind=AssetKind.DOCUMENT,
            owner_id="u1", owner_department="People", business_domain="HR",
            classification="internal", source_system="sharepoint", external_id="doc-1",
        )))
        self.assertIsNone(await repo.get_asset("t2", asset.asset_id))

    async def test_lineage_cycle_and_cross_tenant_rejected(self):
        repo = InMemoryKnowledgeAssetRepository()
        nodes = []
        for kind, name in ((AssetKind.SOURCE_SYSTEM, "SharePoint"),
                           (AssetKind.DOCUMENT, "Policy"), (AssetKind.EMBEDDING, "Vector")):
            node = KnowledgeAsset("t1", name, kind, "u1", "IT", "HR", "internal")
            await repo.register_asset(node)
            nodes.append(node)
        edge = KnowledgeLineageEdge("t1", nodes[0].asset_id, nodes[1].asset_id,
                                    LineageRelation.COLLECTED_FROM, "u1")
        self.assertTrue(await repo.add_lineage(edge))
        self.assertTrue(await repo.add_lineage(KnowledgeLineageEdge(
            "t1", nodes[1].asset_id, nodes[2].asset_id,
            LineageRelation.EMBEDDED_FROM, "u1")))
        with self.assertRaises(ValueError):
            await repo.add_lineage(KnowledgeLineageEdge(
                "t1", nodes[2].asset_id, nodes[0].asset_id,
                LineageRelation.DERIVED_FROM, "u1"))
        with self.assertRaises(ValueError):
            await repo.add_lineage(KnowledgeLineageEdge(
                "t2", nodes[0].asset_id, nodes[1].asset_id,
                LineageRelation.DERIVED_FROM, "u1"))
        chain = await repo.get_lineage("t1", nodes[0].asset_id, direction="downstream", depth=3)
        self.assertEqual(len(chain), 2)

    async def test_project_boundary_and_depth_validation(self):
        repo = InMemoryKnowledgeAssetRepository()
        a = KnowledgeAsset("t1", "A", AssetKind.ASSET, "u1", "IT", "Ops", "internal", project_id="p1")
        b = KnowledgeAsset("t1", "B", AssetKind.DATASET, "u1", "IT", "Ops", "internal", project_id="p2")
        await repo.register_asset(a)
        await repo.register_asset(b)
        with self.assertRaises(ValueError):
            await repo.add_lineage(KnowledgeLineageEdge("t1", a.asset_id, b.asset_id,
                LineageRelation.DERIVED_FROM, "u1", project_id="p1"))
        with self.assertRaises(ValueError):
            await repo.get_lineage("t1", a.asset_id, depth=0)


class KnowledgeAssetApiTests(unittest.TestCase):
    def setUp(self):
        async def resolve(request):
            return KnowledgePrincipal(
                tenant_id=request.headers.get("x-tenant", "t1"),
                user_id="u1", project_ids=frozenset({"p1"}),
                manage_project_ids=frozenset(), permissions=frozenset(request.headers.get("x-permissions", "").split()),
            )
        self.repo = InMemoryKnowledgeAssetRepository()
        app = FastAPI()
        app.include_router(create_knowledge_asset_router(repository=self.repo, resolve_principal=resolve))
        self.client = TestClient(app)

    def test_registration_requires_permission_and_lineage_is_tenant_scoped(self):
        payload = {"name": "HR docs", "kind": "dataset", "owner_department": "People",
                   "business_domain": "HR", "classification": "internal"}
        self.assertEqual(self.client.post("/api/platform/v1/knowledge/assets", json=payload).status_code, 403)
        response = self.client.post("/api/platform/v1/knowledge/assets", json=payload,
                                    headers={"x-permissions": "knowledge.manage"})
        self.assertEqual(response.status_code, 201, response.text)
        asset_id = response.json()["asset_id"]
        self.assertEqual(self.client.get(f"/api/platform/v1/knowledge/assets/{asset_id}",
                          headers={"x-tenant": "t2"}).status_code, 404)
        self.assertEqual(self.client.get(f"/api/platform/v1/knowledge/assets/{asset_id}/lineage",
                          headers={"x-tenant": "t2"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
