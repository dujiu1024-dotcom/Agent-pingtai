from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4

from agent_platform.quotas.run_ledger import (
    PostgresRunBudgetLedger, _validate_budget, _validate_counter, _validate_money,
)


class RunBudgetLedgerReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_model_calls_returns_estimates_actuals_and_deltas(self):
        run_id, call_id = str(uuid4()), uuid4()
        now = datetime.now(timezone.utc)

        class Cursor:
            async def fetchall(self):
                return [{
                    "call_id": call_id, "tenant_id": "tenant-a", "run_id": UUID(run_id),
                    "model_key": "model-a", "estimated_input_tokens": 80,
                    "estimated_output_tokens": 20, "estimated_cost_usd": Decimal("0.50"),
                    "actual_input_tokens": 60, "actual_output_tokens": 15,
                    "actual_cost_usd": Decimal("0.40"), "token_delta": -25,
                    "cost_delta_usd": Decimal("-0.10"), "remaining_tokens": 900,
                    "remaining_cost_usd": Decimal("4.60"), "status": "settled",
                    "overrun_reason": None, "created_at": now, "settled_at": now,
                }]

        class Connection:
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None
            async def execute(self, statement, params):
                self.statement, self.params = statement, params
                return Cursor()

        connection = Connection()

        async def connect():
            return connection

        ledger = PostgresRunBudgetLedger(connect)
        rows = await ledger.list_model_calls(tenant_id="tenant-a", run_id=run_id)
        self.assertIn("WHERE tenant_id=%s AND run_id=%s", connection.statement)
        self.assertEqual(connection.params, ("tenant-a", UUID(run_id), 200))
        self.assertEqual(rows[0]["estimated_total_tokens"], 100)
        self.assertEqual(rows[0]["actual_total_tokens"], 75)
        self.assertEqual(rows[0]["token_delta"], -25)
        self.assertEqual(rows[0]["cost_delta_usd"], "-0.10")
        self.assertEqual(rows[0]["status"], "settled")

    async def test_list_model_calls_validates_tenant_run_and_limit(self):
        ledger = PostgresRunBudgetLedger(lambda: None)
        with self.assertRaises(ValueError):
            await ledger.list_model_calls(tenant_id="", run_id=str(uuid4()))
        with self.assertRaises(ValueError):
            await ledger.list_model_calls(tenant_id="tenant-a", run_id="not-a-uuid")
        with self.assertRaises(ValueError):
            await ledger.list_model_calls(tenant_id="tenant-a", run_id=str(uuid4()), limit=0)


class RunBudgetLedgerValidationTests(unittest.TestCase):
    def test_budget_rejects_invalid_ids_and_negative_limits(self):
        with self.assertRaises(ValueError):
            _validate_budget("tenant", "not-a-uuid", 10, Decimal("1"))
        with self.assertRaises(ValueError):
            _validate_budget("tenant", str(uuid4()), -1, Decimal("1"))

    def test_counters_reject_bool_and_negative_values(self):
        for value in (True, -1, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validate_counter(value, "tokens")

    def test_money_rejects_non_decimal_and_negative(self):
        for value in (1, Decimal("-0.1"), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _validate_money(value, "cost")

    def test_budget_accepts_zero_and_decimal_limits(self):
        _validate_budget("tenant", str(uuid4()), 0, Decimal("0"))


if __name__ == "__main__":
    unittest.main()
