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

    async def test_context_window_can_be_resolved_per_model_request(self):
        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=10000, hard_cost_usd=Decimal("1.00"),
        ))
        inner = FakeHarness()
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=service,
            estimate_request=lambda _request: (9000, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.02"),
            context_window_tokens=lambda request: request.context.model_context_window_tokens,
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-model-window",
            model_template=SimpleNamespace(template_id="general", version=1),
            model_context_window_tokens=16384,
            project_id=None, agent_id="agent-a",
        ))
        await adapter.run(request)
        self.assertEqual(inner.calls, 1)

    async def test_invalid_context_window_is_rejected_at_composition(self):
        with self.assertRaises(ValueError):
            QuotaEnforcedHarnessAdapter(
                FakeHarness(), quota_service=QuotaService(InMemoryQuotaStore()),
                estimate_request=lambda _request: (1, Decimal("0")),
                estimate_actual_cost=lambda _request, _result: Decimal("0"),
                context_window_tokens=0,
            )

    async def test_settlement_failure_keeps_reservation_for_reconciliation(self):
        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=1000, hard_cost_usd=Decimal("1.00"), hard_concurrency=1,
        ))
        original_settle = store.settle
        async def broken_settle(reservation_id, event):
            raise RuntimeError("usage ledger temporarily unavailable")
        store.settle = broken_settle
        inner = FakeHarness()
        adapter = QuotaEnforcedHarnessAdapter(
            inner, quota_service=service,
            estimate_request=lambda _request: (100, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.02"),
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-accounting-failure",
            model_template=SimpleNamespace(template_id="general", version=1),
            project_id=None, agent_id="agent-a",
        ))
        with self.assertRaisesRegex(RuntimeError, "ledger temporarily unavailable"):
            await adapter.run(request)
        self.assertEqual(inner.calls, 1)
        tokens, cost, concurrency = await store.usage(
            "tenant-a", "tenant", "tenant-a",
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        self.assertEqual((tokens, cost, concurrency), (100, Decimal("0.10"), 1))
        store.settle = original_settle

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


    async def test_missing_provider_usage_fails_closed_and_keeps_reservation(self):
        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=1000, hard_cost_usd=Decimal("1.00"), hard_concurrency=1,
        ))

        class MissingUsageHarness:
            async def run(self, _request):
                return SimpleNamespace(usage={}, tool_calls=())

        adapter = QuotaEnforcedHarnessAdapter(
            MissingUsageHarness(), quota_service=service,
            estimate_request=lambda _request: (100, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.02"),
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-missing-usage",
            model_template=SimpleNamespace(template_id="general", version=1),
            project_id=None, agent_id="agent-a",
        ))
        with self.assertRaisesRegex(ValueError, "input_tokens"):
            await adapter.run(request)
        tokens, cost, concurrency = await store.usage(
            "tenant-a", "tenant", "tenant-a",
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        self.assertEqual((tokens, cost, concurrency), (100, Decimal("0.10"), 1))

    async def test_boolean_provider_usage_is_rejected(self):
        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=1000, hard_cost_usd=Decimal("1.00"),
        ))

        class InvalidUsageHarness:
            async def run(self, _request):
                return SimpleNamespace(
                    usage={"input_tokens": True, "output_tokens": 5},
                    tool_calls=(),
                )

        adapter = QuotaEnforcedHarnessAdapter(
            InvalidUsageHarness(), quota_service=service,
            estimate_request=lambda _request: (100, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.02"),
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-invalid-usage",
            model_template=SimpleNamespace(template_id="general", version=1),
            project_id=None, agent_id="agent-a",
        ))
        with self.assertRaisesRegex(ValueError, "input_tokens"):
            await adapter.run(request)


    async def test_split_estimator_is_reserved_and_settled(self):
        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        service = QuotaService(store)
        await service.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=1000, hard_input_tokens=80, hard_output_tokens=40,
            hard_cost_usd=Decimal("1.00"),
        ))

        class Harness:
            async def run(self, _request):
                return SimpleNamespace(
                    usage={"input_tokens": 30, "output_tokens": 10},
                    tool_calls=(),
                )

        adapter = QuotaEnforcedHarnessAdapter(
            Harness(), quota_service=service,
            estimate_request=lambda _request: (80, 40, Decimal("0.10")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.02"),
        )
        request = SimpleNamespace(context=SimpleNamespace(
            tenant_id="tenant-a", run_id="run-split", agent_id="agent-a",
            project_id=None,
            model_template=SimpleNamespace(template_id="general", version=1),
        ))
        await adapter.run(request)
        tokens, _cost, concurrency = await store.usage(
            "tenant-a", "tenant", "tenant-a",
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        self.assertEqual((tokens, concurrency), (40, 0))


if __name__ == "__main__":
    unittest.main()
