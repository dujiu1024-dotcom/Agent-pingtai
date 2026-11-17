from __future__ import annotations

import unittest
from datetime import datetime, timezone
from decimal import Decimal

from agent_platform.quotas.service import UsageEvent
from agent_platform.quotas.usage_reporting import build_usage_report


class UsageReportingTests(unittest.TestCase):
    def setUp(self):
        self.events = [
            UsageEvent(
                event_id="e1", tenant_id="t1", run_id="r1", model_key="model-a",
                input_tokens=100, output_tokens=50, cost_usd=Decimal("0.010"),
                occurred_at=datetime(2026, 11, 17, tzinfo=timezone.utc),
                project_id="p1", agent_id="a1", tool_calls=2, runtime_ms=1000,
                storage_bytes=128, price_card_version="2026-11", source="estimated",
            ),
            UsageEvent(
                event_id="e2", tenant_id="t1", run_id="r2", model_key="model-a",
                input_tokens=200, output_tokens=100, cost_usd=Decimal("0.025"),
                occurred_at=datetime(2026, 11, 17, tzinfo=timezone.utc),
                project_id="p1", agent_id="a2", tool_calls=3, runtime_ms=2000,
                storage_bytes=256, price_card_version="2026-12", source="provider_reported",
            ),
            UsageEvent(
                event_id="e3", tenant_id="t1", run_id="r3", model_key="model-b",
                input_tokens=10, output_tokens=5, cost_usd=Decimal("0.002"),
                occurred_at=datetime(2026, 11, 17, tzinfo=timezone.utc),
                tool_calls=1, runtime_ms=500, storage_bytes=64, source="reconciled",
            ),
        ]

    def test_aggregates_model_tool_storage_and_attribution_dimensions(self):
        report = build_usage_report(self.events)
        self.assertEqual(report["events"], 3)
        self.assertEqual(report["total_tokens"], 465)
        self.assertEqual(report["tool_calls"], 6)
        self.assertEqual(report["runtime_ms"], 3500)
        self.assertEqual(report["storage_bytes"], 448)
        self.assertEqual(report["by_model"]["model-a"]["events"], 2)
        self.assertEqual(report["by_project"]["p1"]["tool_calls"], 5)
        self.assertEqual(report["by_agent"]["a2"]["storage_bytes"], 256)
        self.assertEqual(report["price_card_versions"], ["2026-11", "2026-12"])

    def test_keeps_estimate_actual_and_reconciled_costs_separate(self):
        report = build_usage_report(self.events)
        self.assertEqual(report["estimated_cost_usd"], "0.010")
        self.assertEqual(report["provider_reported_cost_usd"], "0.025")
        self.assertEqual(report["reconciled_event_cost_usd"], "0.002")
        self.assertEqual(report["estimated_vs_provider_actual_variance_usd"], "0.015")

    def test_accepts_one_shot_iterators_and_empty_input(self):
        report = build_usage_report(iter(self.events))
        self.assertEqual(report["events"], 3)
        empty = build_usage_report([])
        self.assertEqual(empty["events"], 0)
        self.assertEqual(empty["estimated_cost_usd"], "0")
        self.assertEqual(empty["by_model"], {})

    def test_rejects_non_ledger_values(self):
        with self.assertRaises(TypeError):
            build_usage_report([object()])


if __name__ == "__main__":
    unittest.main()
