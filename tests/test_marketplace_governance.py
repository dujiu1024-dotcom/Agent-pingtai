from __future__ import annotations

import unittest

from agent_platform.marketplace.governance import (
    InMemoryMarketplaceStore, MarketplaceGateError, MarketplaceGovernance,
)


class MarketplaceGovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryMarketplaceStore()
        self.calls = []
        async def yes(**kwargs): return True
        async def audit(**kwargs): self.calls.append(("audit", kwargs["action"]))
        async def trial(**kwargs):
            self.calls.append(("trial", kwargs))
            return {"passed": True, "network_disabled": True, "host_mounts": False,
                    "output_digest": "a" * 64}
        self.service = MarketplaceGovernance(
            store=self.store, verify_signature=yes, verify_provenance=yes,
            scan_package=yes, evaluate_asset=yes, sandbox_trial=trial,
            verify_installation=yes, audit=audit,
        )

    async def submit(self, **overrides):
        values = dict(tenant_id="tenant-a", publisher_id="publisher-a", actor_id="publisher-a",
            asset_id="sales-assistant", asset_kind="digital_employee", version="1.0.0",
            name="Sales Assistant", description="Sales workflow", digest="b" * 64,
            signature="detached-signature", visibility="public", license_id="enterprise")
        values.update(overrides)
        return await self.service.submit(**values)

    async def test_submission_requires_signature_provenance_scan_and_evaluation(self):
        async def no(**kwargs): return False
        service = MarketplaceGovernance(store=self.store, verify_signature=no,
            verify_provenance=no, scan_package=no, evaluate_asset=no,
            sandbox_trial=no, verify_installation=no, audit=no)
        with self.assertRaises(MarketplaceGateError):
            await service.submit(tenant_id="tenant-a", publisher_id="publisher-a",
                actor_id="publisher-a", asset_id="sales-assistant", asset_kind="digital_employee",
                version="1.0.0", name="Sales", description="", digest="b"*64,
                signature="sig", visibility="public", license_id="free")
        self.assertEqual(self.store.assets, {})

    async def test_independent_review_and_isolated_trial_required_before_publish(self):
        await self.submit()
        with self.assertRaises(MarketplaceGateError):
            await self.service.publish(tenant_id="tenant-a", asset_id="sales-assistant",
                version="1.0.0", actor_id="publisher-a")
        with self.assertRaises(MarketplaceGateError):
            await self.service.review(tenant_id="tenant-a", asset_id="sales-assistant",
                version="1.0.0", reviewer_id="publisher-a", approved=True, reason="self approval")
        await self.service.review(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", reviewer_id="reviewer-a", approved=True, reason="policy checks passed")
        await self.service.trial(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", actor_id="reviewer-a", trial_config={})
        published = await self.service.publish(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", actor_id="reviewer-a")
        self.assertEqual(published.status, "published")
        self.assertFalse(self.calls[0][1] == "marketplace.publish")

    async def test_trial_rejects_missing_isolation_evidence(self):
        await self.submit()
        await self.service.review(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", reviewer_id="reviewer-a", approved=True, reason="reviewed")
        async def unsafe(**kwargs): return {"passed": True, "network_disabled": False, "host_mounts": True, "credentials_injected": False}
        self.service.sandbox_trial = unsafe
        with self.assertRaises(MarketplaceGateError):
            await self.service.trial(tenant_id="tenant-a", asset_id="sales-assistant",
                version="1.0.0", actor_id="reviewer-a", trial_config={})

    async def test_versions_are_immutable_and_tenant_scoped(self):
        await self.submit()
        with self.assertRaises(ValueError):
            await self.submit()
        self.assertIsNone(await self.store.get("tenant-b", "sales-assistant", "1.0.0"))
        await self.submit(version="1.1.0")
        self.assertEqual(len(await self.service.versions(tenant_id="tenant-a",
            asset_id="sales-assistant")), 2)

    async def test_ratings_require_verified_installation_and_prevent_duplicates(self):
        await self.submit()
        await self.service.review(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", reviewer_id="reviewer-a", approved=True, reason="reviewed")
        await self.service.trial(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", actor_id="reviewer-a", trial_config={})
        await self.service.publish(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", actor_id="reviewer-a")
        rating = await self.service.rate(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", installation_id="install-a", user_id="user-a", score=5)
        self.assertEqual(rating["rating_average"], 5.0)
        with self.assertRaises(ValueError):
            await self.service.rate(tenant_id="tenant-a", asset_id="sales-assistant",
                version="1.0.0", installation_id="install-a", user_id="user-a", score=5)

    async def test_publisher_cannot_rate_own_asset(self):
        await self.submit()
        await self.service.review(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", reviewer_id="reviewer-a", approved=True, reason="reviewed")
        await self.service.trial(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", actor_id="reviewer-a", trial_config={})
        await self.service.publish(tenant_id="tenant-a", asset_id="sales-assistant",
            version="1.0.0", actor_id="reviewer-a")
        with self.assertRaises(MarketplaceGateError):
            await self.service.rate(tenant_id="tenant-a", asset_id="sales-assistant",
                version="1.0.0", installation_id="install-pub", user_id="publisher-a", score=5)
