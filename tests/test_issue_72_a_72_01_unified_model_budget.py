from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from agent_platform.quotas.service import (
    BudgetExceeded,
    InMemoryQuotaStore,
    QuotaService,
    SAFE_DEFAULT_BUDGETS,
)


class UnifiedModelBudgetAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_tenant_project_agent_and_run_scopes_get_persisted_safe_defaults(self):
        store = InMemoryQuotaStore()
        quotas = QuotaService(store)
        scopes = (
            ("tenant", "tenant-a", "tenant"),
            ("project", "project-a", "project"),
            ("agent", "agent-a", "agent"),
            ("run", "run-a", "run"),
        )
        now = datetime.now(timezone.utc)

        for scope_type, scope_id, policy_key in scopes:
            with self.subTest(scope=scope_type):
                reservation, warnings = await quotas.reserve_run(
                    tenant_id="tenant-a",
                    run_id="run-a",
                    scope_type=scope_type,
                    scope_id=scope_id,
                    estimated_tokens=3,
                    estimated_input_tokens=2,
                    estimated_output_tokens=1,
                    estimated_cost_usd=Decimal("0.01"),
                )
                budget = await store.get_budget(
                    "tenant-a", scope_type, scope_id, now,
                )
                policy = SAFE_DEFAULT_BUDGETS[policy_key]
                self.assertIsNotNone(budget)
                self.assertEqual(budget.hard_input_tokens, policy.input_tokens)
                self.assertEqual(budget.hard_context_window_tokens, policy.context_window_tokens)
                self.assertEqual(budget.hard_output_tokens, policy.output_tokens)
                self.assertEqual(budget.hard_cost_usd, policy.cost_usd)
                self.assertEqual((reservation.reserved_input_tokens,
                                  reservation.reserved_output_tokens), (2, 1))
                self.assertEqual(warnings, ())
                await quotas.release_run(reservation)

    async def test_scope_without_a_safe_default_fails_closed(self):
        quotas = QuotaService(InMemoryQuotaStore())
        with self.assertRaisesRegex(BudgetExceeded, "no safe default exists"):
            await quotas.reserve_run(
                tenant_id="tenant-a",
                run_id="run-a",
                scope_type="queue",
                scope_id="queue-a",
                estimated_tokens=1,
                estimated_input_tokens=1,
                estimated_output_tokens=0,
                estimated_cost_usd=Decimal("0.01"),
            )


if __name__ == "__main__":
    unittest.main()
