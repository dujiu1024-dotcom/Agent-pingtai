from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from agent_platform.quotas.harness import QuotaEnforcedHarnessAdapter
from agent_platform.quotas.service import Budget, BudgetExceeded, InMemoryQuotaStore, QuotaService


class FakeHarness:
    def __init__(self):
        self.calls = 0
    async def run(self, request):
        self.calls += 1
        return SimpleNamespace(
            usage={"input_tokens": 10, "output_tokens": 5},
            tool_calls=(),
        )


class QuotaHarnessEnforcementTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_budget_blocks_execution_before_model_call(self):
        inner = FakeHarness()
        service = QuotaService(InMemoryQuotaStore())
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=service,
            estimate_request=lambda _request: (100, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.02"),
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-a",
            model_template=SimpleNamespace(template_id="general", version=1),
            project_id=None, agent_id="agent-a",
        ))
        with self.assertRaises(BudgetExceeded):
            await adapter.run(request)
        self.assertEqual(inner.calls, 0)

    async def test_budget_reserves_then_settles_actual_usage(self):
        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=1000, hard_cost_usd=Decimal("1.00"), hard_concurrency=2,
        ))
        inner = FakeHarness()
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=service,
            estimate_request=lambda _request: (100, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.02"),
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-a",
            model_template=SimpleNamespace(template_id="general", version=1),
            project_id=None, agent_id="agent-a",
        ))
        await adapter.run(request)
        self.assertEqual(inner.calls, 1)
        tokens, cost, concurrency = await store.usage(
            "tenant-a", "tenant", "tenant-a",
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        self.assertEqual(tokens, 15)
        self.assertEqual(cost, Decimal("0.02"))
        self.assertEqual(concurrency, 0)

    async def test_context_window_limit_blocks_before_model_dispatch(self):
        inner = FakeHarness()
        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=10000, hard_cost_usd=Decimal("10.00"),
        ))
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=service,
            estimate_request=lambda _request: (9000, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.02"),
            context_window_tokens=8192,
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-window",
            model_template=SimpleNamespace(template_id="general", version=1),
            project_id=None, agent_id="agent-a",
        ))
        with self.assertRaises(BudgetExceeded):
            await adapter.run(request)
        self.assertEqual(inner.calls, 0)
        tokens, cost, concurrency = await store.usage(
            "tenant-a", "tenant", "tenant-a",
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        self.assertEqual((tokens, cost, concurrency), (0, Decimal("0"), 0))

    async def test_invalid_context_window_is_rejected_at_composition(self):
        with self.assertRaises(ValueError):
            QuotaEnforcedHarnessAdapter(
                FakeHarness(), quota_service=QuotaService(InMemoryQuotaStore()),
                estimate_request=lambda _request: (1, Decimal("0")),
                estimate_actual_cost=lambda _request, _result: Decimal("0"),
                context_window_tokens=0,
            )

    async def test_tenant_scope_cannot_be_overridden_by_untrusted_resolver(self):
        inner = FakeHarness()
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=QuotaService(InMemoryQuotaStore()),
            estimate_request=lambda _request: (1, Decimal("0.01")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.01"),
            scope_resolver=lambda _request: ("tenant", "tenant-b"),
        )
        request = SimpleNamespace(context=SimpleNamespace(tenant_id="tenant-a", run_id="run-a"))
        with self.assertRaises(ValueError):
            await adapter.run(request)
        self.assertEqual(inner.calls, 0)


if __name__ == "__main__":
    unittest.main()
