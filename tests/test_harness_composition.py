from __future__ import annotations
import unittest
from decimal import Decimal
from agent_platform.runtime.harness_factory import compose_quota_enforced_harness
from agent_platform.quotas.harness import QuotaEnforcedHarnessAdapter

class HarnessCompositionTests(unittest.TestCase):
    def test_composition_always_returns_quota_enforcing_adapter(self):
        inner = object()
        quota_service = object()
        adapter = compose_quota_enforced_harness(
            inner, quota_service=quota_service,
            estimate_request=lambda request: (10, Decimal("0.01")),
            estimate_actual_cost=lambda request, result: Decimal("0.02"),
        )
        self.assertIsInstance(adapter, QuotaEnforcedHarnessAdapter)
        self.assertIs(adapter.inner, inner)
        self.assertIs(adapter.quota_service, quota_service)
        self.assertEqual(adapter.context_window_tokens, 8192)

    def test_missing_estimators_or_quota_service_are_rejected(self):
        with self.assertRaises(ValueError):
            compose_quota_enforced_harness(
                object(), quota_service=None,
                estimate_request=lambda request: (0, Decimal("0")),
                estimate_actual_cost=lambda request, result: Decimal("0"),
            )
        with self.assertRaises(ValueError):
            compose_quota_enforced_harness(
                object(), quota_service=object(), estimate_request=None,
                estimate_actual_cost=lambda request, result: Decimal("0"),
            )

    def test_context_window_is_configurable_through_composition(self):
        adapter = compose_quota_enforced_harness(
            object(), quota_service=object(),
            estimate_request=lambda request: (10, Decimal("0.01")),
            estimate_actual_cost=lambda request, result: Decimal("0.02"),
            context_window_tokens=16384,
        )
        self.assertEqual(adapter.context_window_tokens, 16384)

if __name__ == "__main__":
    unittest.main()
