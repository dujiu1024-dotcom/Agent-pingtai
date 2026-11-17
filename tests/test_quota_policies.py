from __future__ import annotations

import unittest

from agent_platform.quotas.policies import (
    InMemoryQuotaPolicyRepository,
    QuotaPolicy,
    QuotaPolicyContext,
    QuotaPolicyService,
)


class HierarchicalQuotaPolicyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemoryQuotaPolicyRepository()
        self.service = QuotaPolicyService(self.repository)

    async def test_specificity_overrides_parent_and_merges_resource_dimensions(self):
        for policy in (
            QuotaPolicy("defaults", "default", "*", {"tokens": 1000, "requests": 100}),
            QuotaPolicy("tenant", "tenant", "t-1", {"tokens": 800, "cost_usd": 20}),
            QuotaPolicy("project", "project", "p-1", {"tokens": 500, "storage_bytes": 2048}),
            QuotaPolicy("subject", "subject", "u-1", {"concurrency": 2}),
        ):
            await self.service.configure(policy, actor_id="admin", authorize=lambda _: True)
        resolved = await self.service.resolve(QuotaPolicyContext("t-1", "p-1", "u-1"))
        self.assertEqual(resolved.limits, {
            "tokens": 500, "cost_usd": 20, "storage_bytes": 2048,
            "concurrency": 2, "requests": 100,
        })
        self.assertEqual(resolved.sources["tokens"], "project")

    async def test_priority_breaks_ties_inside_same_scope(self):
        await self.service.configure(
            QuotaPolicy("low", "tenant", "t-1", {"tokens": 100}, priority=10),
            actor_id="admin", authorize=lambda _: True,
        )
        await self.service.configure(
            QuotaPolicy("high", "tenant", "t-1", {"tokens": 50}, priority=20),
            actor_id="admin", authorize=lambda _: True,
        )
        resolved = await self.service.resolve(QuotaPolicyContext("t-1"))
        self.assertEqual(resolved.limits["tokens"], 50)
        self.assertEqual(resolved.sources["tokens"], "high")

    async def test_tenant_isolation_and_disabled_policy(self):
        await self.service.configure(
            QuotaPolicy("foreign", "tenant", "t-2", {"tokens": 1}),
            actor_id="admin", authorize=lambda _: True,
        )
        await self.service.configure(
            QuotaPolicy("disabled", "tenant", "t-1", {"tokens": 2}, enabled=False),
            actor_id="admin", authorize=lambda _: True,
        )
        resolved = await self.service.resolve(QuotaPolicyContext("t-1"))
        self.assertNotIn("tokens", resolved.limits)

    async def test_configuration_requires_authorization_and_writes_audit(self):
        policy = QuotaPolicy("tenant", "tenant", "t-1", {"tokens": 10})
        with self.assertRaises(PermissionError):
            await self.service.configure(policy, actor_id="user", authorize=lambda _: False)
        self.assertEqual(self.repository.audit_events, [])
        await self.service.configure(policy, actor_id="admin", authorize=lambda _: True)
        self.assertEqual(len(self.repository.audit_events), 1)
        event = self.repository.audit_events[0]
        self.assertEqual(event.action, "quota.policy.created")
        self.assertEqual(len(event.payload_sha256), 64)

    async def test_invalid_resource_dimension_and_nonfinite_values_rejected(self):
        with self.assertRaises(ValueError):
            QuotaPolicy("bad", "tenant", "t-1", {"memory": 10})
        with self.assertRaises(ValueError):
            QuotaPolicy("bad", "tenant", "t-1", {"tokens": float("nan")})


if __name__ == "__main__":
    unittest.main()
