from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.observability.prompt_rollouts import InMemoryPromptRolloutRepository, PromptRolloutService
from agent_platform.observability.prompts import PromptVariant, tenant_prompt_key


class FakeVariantRegistry:
    def __init__(self):
        self.variants = {}

    def add(self, tenant_id, prompt_id, name, status="active"):
        key = tenant_prompt_key(tenant_id, prompt_id)
        self.variants.setdefault(key, []).append(PromptVariant(key, name, 1, status, "admin", "admin"))

    def list_variants(self, prompt_id):
        return tuple(self.variants.get(prompt_id, ()))


class PromptRolloutTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry = FakeVariantRegistry()
        for tenant in ("tenant-a", "tenant-b"):
            for variant in ("stable", "candidate", "tagged"):
                self.registry.add(tenant, "assistant", variant)
        self.repository = InMemoryPromptRolloutRepository()
        self.service = PromptRolloutService(registry=self.registry, repository=self.repository)

    async def test_percentage_routing_is_sticky_and_audited(self):
        await self.service.configure(tenant_id="tenant-a", prompt_id="assistant",
            weights={"stable": 80, "candidate": 20}, actor="operator")
        first = await self.service.route(tenant_id="tenant-a", prompt_id="assistant", routing_key="user-123")
        second = await self.service.route(tenant_id="tenant-a", prompt_id="assistant", routing_key="user-123")
        self.assertEqual(first.variant, second.variant)
        self.assertEqual(first.rule, "percentage")
        self.assertEqual(first.subject_hash, second.subject_hash)
        self.assertEqual(len(await self.service.audit(tenant_id="tenant-a", prompt_id="assistant")), 2)

    async def test_tenant_and_tag_routes_precede_percentage(self):
        await self.service.configure(tenant_id="tenant-a", prompt_id="assistant",
            weights={"stable": 100}, actor="operator", tag_routes={"beta": "tagged"},
            tenant_routes={"tenant-b": "candidate"})
        tenant = await self.service.route(tenant_id="tenant-b", policy_tenant_id="tenant-a",
            prompt_id="assistant", routing_key="user-1")
        tagged = await self.service.route(tenant_id="tenant-a", prompt_id="assistant",
            routing_key="user-2", user_tags=("beta",))
        self.assertEqual((tenant.variant, tenant.rule), ("candidate", "tenant"))
        self.assertEqual((tagged.variant, tagged.rule), ("tagged", "tag:beta"))

    async def test_manual_cutover_and_auto_promotion(self):
        await self.service.manual_cutover(tenant_id="tenant-a", prompt_id="assistant",
            variant="candidate", actor="operator")
        policy = await self.repository.get_policy("tenant-a", "assistant")
        self.assertEqual(policy.weights, (("candidate", 100.0),))
        report = SimpleNamespace(tenant_id="tenant-a", prompt_id="assistant", baseline_variant="stable",
            metrics=(SimpleNamespace(variant="stable", average_score=.7, pass_rate=.8, average_latency_ms=20),
                     SimpleNamespace(variant="candidate", average_score=.9, pass_rate=.9, average_latency_ms=25)))
        promoted = await self.service.auto_promote(report=report, actor="automation", minimum_score_gain=.1)
        self.assertEqual(promoted.weights, (("candidate", 100.0),))
        self.assertEqual(promoted.updated_by, "automation")

    async def test_tenant_policy_isolation_and_inactive_variant_rejected(self):
        await self.service.configure(tenant_id="tenant-a", prompt_id="assistant",
            weights={"stable": 100}, actor="operator")
        with self.assertRaises(KeyError):
            await self.service.route(tenant_id="tenant-b", prompt_id="assistant", routing_key="user-1")
        self.registry.add("tenant-a", "assistant", "retired", status="deprecated")
        with self.assertRaises(ValueError):
            await self.service.configure(tenant_id="tenant-a", prompt_id="assistant",
                weights={"retired": 100}, actor="operator")

    async def test_invalid_weights_and_promotion_threshold_fail_closed(self):
        with self.assertRaises(ValueError):
            await self.service.configure(tenant_id="tenant-a", prompt_id="assistant",
                weights={"stable": 90, "candidate": 20}, actor="operator")
        report = SimpleNamespace(tenant_id="tenant-a", prompt_id="assistant", baseline_variant="stable",
            metrics=(SimpleNamespace(variant="stable", average_score=.8, pass_rate=1, average_latency_ms=10),
                     SimpleNamespace(variant="candidate", average_score=.7, pass_rate=1, average_latency_ms=5)))
        with self.assertRaises(ValueError):
            await self.service.auto_promote(report=report, actor="automation")


if __name__ == "__main__":
    unittest.main()
