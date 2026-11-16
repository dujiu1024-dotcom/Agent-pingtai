from __future__ import annotations

import unittest
from decimal import Decimal
from uuid import uuid4

from agent_platform.quotas.run_ledger import (
    RunBudgetExceeded, _validate_budget, _validate_counter, _validate_money,
)


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
