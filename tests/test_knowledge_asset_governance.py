from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.knowledge_asset_governance import create_knowledge_asset_governance_router
from agent_platform.knowledge.asset_governance import (
    FreshnessPolicy, FreshnessState, InMemoryKnowledgeGovernanceRepository,
    KnowledgeAssetGovernanceService, PropagationState, QualityAssessment,
    QualityDimension, SourcePermission,
)
from agent_platform.knowledge.asset_lineage import AssetKind, InMemoryKnowledgeAssetRepository, KnowledgeAsset
from agent_platform.knowledge.contracts import KnowledgePrincipal


class Propagation:
    def __init__(self, result=None):
        self.result = result or {key: True for key in ("knowledge_acl", "retrieval_cache", "vector_index", "downstream_copies")}
        self.calls = []

    async def revoke_asset_access(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class KnowledgeAssetGovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.assets = InMemoryKnowledgeAssetRepository()
        self.asset = KnowledgeAsset("tenant-a", "policy", AssetKind.DOCUMENT, "owner",
            "legal", "compliance", "internal", project_id="p1")
        await self.assets.register_asset(self.asset)
        self.repo = InMemoryKnowledgeGovernanceRepository()
        self.propagation = Propagation()
        self.service = KnowledgeAssetGovernanceService(
            assets=self.assets, repository=self.repo, propagation=self.propagation)

    async def test_quality_score_is_explainable_and_flags_low_dimensions(self):
        scores = {dimension: 90 for dimension in QualityDimension}
        scores[QualityDimension.DUPLICATE] = 20
        evidence = {dimension.value: f"checked {dimension.value}" for dimension in QualityDimension}
        assessment = QualityAssessment("tenant-a", self.asset.asset_id, "reviewer", scores, evidence)
        await self.service.assess_quality(assessment)
        self.assertEqual(assessment.score, 78.33)
        self.assertEqual(assessment.findings, ("duplicate",))
        self.assertEqual((await self.repo.get_quality("tenant-a", self.asset.asset_id)).assessment_id,
            assessment.assessment_id)

    async def test_quality_requires_complete_scores_and_evidence(self):
        with self.assertRaises(ValueError):
            QualityAssessment("tenant-a", self.asset.asset_id, "reviewer", {}, {})

    async def test_freshness_expiry_and_review_due(self):
        now = datetime.now(timezone.utc)
        policy = FreshnessPolicy("tenant-a", self.asset.asset_id, now - timedelta(days=20),
            now + timedelta(days=20), 10, now - timedelta(days=11))
        await self.service.set_freshness(policy)
        self.assertEqual(await self.service.freshness_state("tenant-a", self.asset.asset_id, now),
            FreshnessState.REVIEW_DUE)
        expired = await self.service.freshness_state("tenant-a", self.asset.asset_id,
            now + timedelta(days=21))
        self.assertEqual(expired, FreshnessState.EXPIRED)
        updated = await self.service.review("tenant-a", self.asset.asset_id, now)
        self.assertEqual(updated.last_reviewed_at, now)

    async def test_permission_revocation_records_each_fanout_result(self):
        await self.service.set_source_permission(SourcePermission("tenant-a", self.asset.asset_id,
            "sharepoint", "grant-1", frozenset({"team:legal", "user:42"})))
        receipts = await self.service.revoke_source_permission("tenant-a", self.asset.asset_id)
        self.assertEqual(len(receipts), 4)
        self.assertTrue(all(item.state is PropagationState.SUCCEEDED for item in receipts))
        self.assertEqual(self.propagation.calls[0]["tenant_id"], "tenant-a")
        revoked = await self.repo.get_source_permission("tenant-a", self.asset.asset_id)
        self.assertFalse(revoked.active)
        self.assertEqual(revoked.inherited_principals, frozenset())

    async def test_failed_fanout_is_visible_and_fail_closed(self):
        self.propagation.result = {"knowledge_acl": True, "retrieval_cache": False,
            "vector_index": True, "downstream_copies": False}
        await self.service.set_source_permission(SourcePermission("tenant-a", self.asset.asset_id,
            "sharepoint", "grant-1", frozenset({"team:legal"})))
        receipts = await self.service.revoke_source_permission("tenant-a", self.asset.asset_id)
        self.assertEqual(sum(r.state is PropagationState.FAILED for r in receipts), 2)
        self.assertTrue(all(r.error for r in receipts if r.state is PropagationState.FAILED))

    async def test_tenant_isolation(self):
        self.assertIsNone(await self.repo.get_quality("tenant-b", self.asset.asset_id))
        with self.assertRaises(LookupError):
            await self.service.freshness_state("tenant-b", self.asset.asset_id)


class KnowledgeAssetGovernanceApiTests(unittest.TestCase):
    def setUp(self):
        self.assets = InMemoryKnowledgeAssetRepository()
        self.asset = KnowledgeAsset("tenant-a", "policy", AssetKind.DOCUMENT, "owner",
            "legal", "compliance", "internal", project_id="p1")
        import asyncio
        asyncio.run(self.assets.register_asset(self.asset))
        self.repo = InMemoryKnowledgeGovernanceRepository()
        self.service = KnowledgeAssetGovernanceService(assets=self.assets,
            repository=self.repo, propagation=Propagation())

        async def principal(request):
            return KnowledgePrincipal(tenant_id=request.headers.get("x-tenant", "tenant-a"),
                user_id="reviewer", project_ids=frozenset({"p1"}),
                manage_project_ids=frozenset({"p1"}) if request.headers.get("x-manage") == "yes" else frozenset(),
                permissions=frozenset(request.headers.get("x-permissions", "").split()))
        app = FastAPI()
        app.include_router(create_knowledge_asset_governance_router(service=self.service,
            resolve_principal=principal))
        self.client = TestClient(app)

    def test_quality_api_enforces_permission_and_tenant(self):
        url = f"/api/platform/v1/knowledge/assets/{self.asset.asset_id}/quality"
        payload = {"scores": {d.value: 90 for d in QualityDimension},
            "evidence": {d.value: "reviewed source" for d in QualityDimension}}
        self.assertEqual(self.client.put(url, json=payload).status_code, 403)
        response = self.client.put(url, json=payload, headers={"x-manage": "yes"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["score"], 90)
        self.assertEqual(self.client.get(url, headers={"x-tenant": "tenant-b"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
