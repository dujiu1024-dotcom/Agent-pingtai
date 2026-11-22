from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from agent_platform.quotas import (
    Budget,
    BudgetExceeded,
    InMemoryQuotaStore,
    QuotaEnforcedHarnessAdapter,
    QuotaService,
    UsageEvent,
)


class QuotaServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.start = datetime.now(timezone.utc) - timedelta(minutes=1)
        self.end = self.start + timedelta(days=1)
        self.budget = Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=self.start, period_end=self.end,
            hard_tokens=100, hard_cost_usd=Decimal("1.00"), hard_concurrency=1,
            soft_tokens=80,
        )
        self.store = InMemoryQuotaStore()
        self.service = QuotaService(self.store)
        await self.service.configure(self.budget)

    async def reserve(self, run_id: str, tokens: int, cost: str):
        return await self.service.reserve_run(
            tenant_id="tenant-a", run_id=run_id, scope_type="tenant", scope_id="tenant-a",
            estimated_tokens=tokens, estimated_cost_usd=Decimal(cost),
        )

    async def test_server_reservation_enforces_hard_tokens_and_concurrency(self) -> None:
        first, warnings = await self.reserve("run-1", 80, "0.50")
        self.assertIn("soft token budget exceeded", warnings)
        with self.assertRaises(BudgetExceeded):
            await self.reserve("run-2", 1, "0.01")
        self.assertTrue(await self.service.release_run(first))
        second, _ = await self.reserve("run-2", 100, "1.00")
        self.assertEqual(second.run_id, "run-2")

    async def test_atomic_reservations_prevent_concurrent_overbooking(self) -> None:
        async def attempt(run_id: str):
            try:
                return await self.reserve(run_id, 60, "0.60")
            except BudgetExceeded:
                return None
        results = await asyncio.gather(attempt("run-1"), attempt("run-2"))
        self.assertEqual(sum(result is not None for result in results), 1)

    async def test_settlement_is_tenant_run_bound_and_releases_reservation(self) -> None:
        reservation, _ = await self.reserve("run-1", 90, "0.90")
        with self.assertRaises(PermissionError):
            await self.store.settle(
                reservation.reservation_id,
                UsageEvent("e1", "tenant-b", "run-1", "model-x", 1, 1, Decimal("0.1"), self.start),
            )
        event = await self.service.settle_run(
            reservation, model_key="model-x", input_tokens=30, output_tokens=20,
            cost_usd=Decimal("0.25"), occurred_at=self.start,
        )
        self.assertEqual(event.total_tokens, 50)
        tokens, cost, concurrency = await self.store.usage(
            "tenant-a", "tenant", "tenant-a", self.start, self.end,
        )
        self.assertEqual((tokens, cost, concurrency), (50, Decimal("0.25"), 0))
        exported = await self.store.list_usage(
            "tenant-a", self.start, self.end, run_id="run-1", model_key="model-x",
        )
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0].total_tokens, 50)
        self.assertEqual(
            await self.store.list_usage("tenant-b", self.start, self.end), [],
        )

    async def test_unconfigured_scope_uses_persisted_safe_default(self) -> None:
        reservation, _ = await self.service.reserve_run(
            tenant_id="tenant-b", run_id="run-default-tenant", scope_type="tenant", scope_id="tenant-b",
            estimated_tokens=100, estimated_cost_usd=Decimal("0.01"),
            estimated_input_tokens=80, estimated_output_tokens=20,
        )
        self.assertEqual(reservation.tenant_id, "tenant-b")
        budget = await self.store.get_budget(
            "tenant-b", "tenant", "tenant-b", datetime.now(timezone.utc),
        )
        self.assertIsNotNone(budget)
        self.assertEqual(budget.hard_input_tokens, 500_000)
        self.assertEqual(budget.hard_context_window_tokens, 32_768)
        self.assertEqual(budget.hard_output_tokens, 100_000)
        self.assertEqual(budget.hard_cost_usd, Decimal("25.00"))

    async def test_agent_project_and_run_scopes_have_distinct_safe_defaults(self) -> None:
        from agent_platform.quotas.service import safe_default_budget

        now = datetime(2026, 11, 16, 12, tzinfo=timezone.utc)
        expected = {
            "project": (250_000, 32_768, 50_000, Decimal("10.00")),
            "agent": (100_000, 16_384, 25_000, Decimal("5.00")),
            "run": (50_000, 16_384, 12_000, Decimal("1.00")),
        }
        for scope_type, values in expected.items():
            budget = safe_default_budget(
                tenant_id="tenant-a", scope_type=scope_type,
                scope_id=f"{scope_type}-a", now=now,
            )
            self.assertIsNotNone(budget)
            self.assertEqual((
                budget.hard_input_tokens, budget.hard_context_window_tokens,
                budget.hard_output_tokens, budget.hard_cost_usd,
            ), values)
            self.assertEqual(budget.period_start, datetime(2026, 11, 1, tzinfo=timezone.utc))
            self.assertEqual(budget.period_end, datetime(2026, 12, 1, tzinfo=timezone.utc))

    async def test_safe_defaults_do_not_apply_to_unapproved_scope_types(self) -> None:
        from agent_platform.quotas.service import safe_default_budget

        self.assertIsNone(safe_default_budget(
            tenant_id="tenant-a", scope_type="queue", scope_id="default",
        ))

    async def test_context_window_budget_rejects_invalid_values(self) -> None:
        with self.assertRaisesRegex(ValueError, "non-negative integers"):
            Budget(
                tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
                period_start=self.start, period_end=self.end,
                hard_context_window_tokens=True,
            )

    async def test_harness_adapter_reserves_then_settles_actual_usage(self) -> None:
        context = SimpleNamespace(
            tenant_id="tenant-a", run_id="run-harness", agent_id="agent-a",
            project_id="project-a",
            model_template=SimpleNamespace(template_id="general", version=2),
        )
        request = SimpleNamespace(context=context)

        class Harness:
            async def run(self, _request):
                return SimpleNamespace(
                    usage={"input_tokens": 30, "output_tokens": 20},
                    tool_calls=[{"name": "search"}],
                    output_text="done",
                )

        adapter = QuotaEnforcedHarnessAdapter(
            Harness(), quota_service=self.service,
            estimate_request=lambda _request: (100, Decimal("0.50")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.25"),
            on_quota_warning=lambda _event: None,
        )
        result = await adapter.run(request)
        self.assertEqual(result.output_text, "done")
        tokens, cost, concurrency = await self.store.usage(
            "tenant-a", "tenant", "tenant-a", self.start, self.end,
        )
        self.assertEqual((tokens, cost, concurrency), (50, Decimal("0.25"), 0))

    async def test_harness_rejects_invalid_split_estimates_before_dispatch(self) -> None:
        context = SimpleNamespace(
            tenant_id="tenant-a", run_id="run-invalid-split", agent_id="agent-a",
            project_id=None,
            model_template=SimpleNamespace(template_id="general", version=1),
        )
        request = SimpleNamespace(context=context)

        class MustNotRunHarness:
            async def run(self, _request):
                raise AssertionError("invalid estimates must fail before provider dispatch")

        for estimate in (
            (-1, 1, Decimal("0.01")),
            (0, True, Decimal("0.01")),
            (1, "1", Decimal("0.01")),
        ):
            adapter = QuotaEnforcedHarnessAdapter(
                MustNotRunHarness(), quota_service=self.service,
                estimate_request=lambda _request, value=estimate: value,
                estimate_actual_cost=lambda _request, _result: Decimal("0.01"),
            )
            with self.subTest(estimate=estimate), self.assertRaises(ValueError):
                await adapter.run(request)

    async def test_harness_failure_releases_quota_reservation(self) -> None:
        context = SimpleNamespace(
            tenant_id="tenant-a", run_id="run-failed", agent_id="agent-a",
            project_id=None,
            model_template=SimpleNamespace(template_id="general", version=1),
        )
        request = SimpleNamespace(context=context)

        class PreDispatchFailure(RuntimeError):
            """The host has proved the provider request was never dispatched."""

        class BrokenHarness:
            async def run(self, _request):
                raise PreDispatchFailure("model failed before provider dispatch")

        adapter = QuotaEnforcedHarnessAdapter(
            BrokenHarness(), quota_service=self.service,
            estimate_request=lambda _request: (50, Decimal("0.20")),
            estimate_actual_cost=lambda _request, _result: Decimal("0.10"),
            release_reservation_on_error=lambda error: isinstance(error, PreDispatchFailure),
        )
        with self.assertRaisesRegex(PreDispatchFailure, "before provider dispatch"):
            await adapter.run(request)
        tokens, cost, concurrency = await self.store.usage(
            "tenant-a", "tenant", "tenant-a", self.start, self.end,
        )
        self.assertEqual((tokens, cost, concurrency), (0, Decimal("0"), 0))

    async def test_harness_rejects_invalid_split_token_estimates_before_dispatch(self) -> None:
        context = SimpleNamespace(
            tenant_id="tenant-a", run_id="run-invalid-split", agent_id="agent-a",
            project_id=None,
            model_template=SimpleNamespace(template_id="general", version=1),
        )
        request = SimpleNamespace(context=context)

        class MustNotRunHarness:
            async def run(self, _request):
                raise AssertionError("invalid token estimates must be rejected before provider dispatch")

        for estimate in (
            (0, -1, Decimal("0.01")),
            (0, True, Decimal("0.01")),
            (1, "1", Decimal("0.01")),
        ):
            adapter = QuotaEnforcedHarnessAdapter(
                MustNotRunHarness(), quota_service=self.service,
                estimate_request=lambda _request, value=estimate: value,
                estimate_actual_cost=lambda _request, _result: Decimal("0.01"),
            )
            with self.subTest(estimate=estimate), self.assertRaises(ValueError):
                await adapter.run(request)

    async def test_explicit_context_window_budget_round_trips(self) -> None:
        configured = Budget(
            tenant_id="tenant-a", scope_type="project", scope_id="project-a",
            period_start=self.start, period_end=self.end,
            hard_input_tokens=12_000, hard_context_window_tokens=8_192,
            hard_output_tokens=2_000, hard_cost_usd=Decimal("3.00"),
        )
        await self.service.configure(configured)
        loaded = await self.store.get_budget(
            "tenant-a", "project", "project-a", datetime.now(timezone.utc),
        )
        self.assertEqual(loaded, configured)

    async def test_negative_usage_rejected(self) -> None:
        reservation, _ = await self.reserve("run-1", 10, "0.10")
        with self.assertRaises(ValueError):
            await self.service.settle_run(
                reservation, model_key="model-x", input_tokens=-1, output_tokens=1,
                cost_usd=Decimal("0.01"),
            )


    async def test_non_finite_budget_estimate_and_actual_costs_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            Budget(
                tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
                period_start=self.start, period_end=self.end,
                hard_cost_usd=Decimal("NaN"),
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            Budget(
                tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
                period_start=self.start, period_end=self.end,
                soft_cost_usd=Decimal("Infinity"),
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            await self.reserve("run-nan", 10, "NaN")
        reservation, _ = await self.reserve("run-actual", 10, "0.10")
        with self.assertRaisesRegex(ValueError, "finite"):
            await self.service.settle_run(
                reservation, model_key="model-x", input_tokens=1, output_tokens=1,
                cost_usd=Decimal("Infinity"),
            )
        with self.assertRaisesRegex(ValueError, "finite"):
            UsageEvent(
                "event-nan", "tenant-a", "run-x", "model-x", 1, 1,
                Decimal("NaN"), self.start,
            )


    async def test_usage_export_rejects_invalid_ranges_filters_and_limits(self) -> None:
        with self.assertRaisesRegex(ValueError, "integer"):
            await self.store.list_usage("tenant-a", self.start, self.end, limit=True)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            await self.store.list_usage(
                "tenant-a", self.start.replace(tzinfo=None), self.end,
            )
        with self.assertRaisesRegex(ValueError, "tenant_id"):
            await self.store.list_usage(" ", self.start, self.end)
        with self.assertRaisesRegex(ValueError, "run_id"):
            await self.store.list_usage(
                "tenant-a", self.start, self.end, run_id=" ",
            )
        with self.assertRaisesRegex(ValueError, "model_key"):
            await self.store.list_usage(
                "tenant-a", self.start, self.end, model_key=" ",
            )

        from agent_platform.quotas.postgres import PostgresQuotaStore
        postgres = PostgresQuotaStore(lambda: self.fail("invalid export must not connect"))
        with self.assertRaisesRegex(ValueError, "integer"):
            await postgres.list_usage("tenant-a", self.start, self.end, limit=True)
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            await postgres.list_usage(
                "tenant-a", self.start.replace(tzinfo=None), self.end,
            )
        with self.assertRaisesRegex(ValueError, "tenant_id"):
            await postgres.list_usage(" ", self.start, self.end)

    async def test_root_run_budget_aggregates_child_run_usage(self) -> None:
        root_run_id = "root-run-1"
        child_run_id = "child-run-1"
        await self.service.configure(Budget(
            tenant_id="tenant-a", scope_type="run", scope_id=root_run_id,
            period_start=self.start, period_end=self.end, hard_tokens=100,
        ))
        reservation, _ = await self.service.reserve_run(
            tenant_id="tenant-a", run_id=child_run_id, root_run_id=root_run_id,
            scope_type="run", scope_id=root_run_id, estimated_tokens=80,
            estimated_cost_usd=Decimal("0.20"),
        )
        event = await self.service.settle_run(
            reservation, model_key="model-child", input_tokens=30, output_tokens=20,
            cost_usd=Decimal("0.10"),
        )
        self.assertEqual(event.root_run_id, root_run_id)
        tokens, cost, concurrency = await self.store.usage(
            "tenant-a", "run", root_run_id, self.start, self.end,
        )
        self.assertEqual((tokens, cost, concurrency), (50, Decimal("0.10"), 0))
        with self.assertRaises(BudgetExceeded):
            await self.service.reserve_run(
                tenant_id="tenant-a", run_id="child-run-2", root_run_id=root_run_id,
                scope_type="run", scope_id=root_run_id, estimated_tokens=51,
                estimated_cost_usd=Decimal("0.10"),
            )
