from datetime import datetime, timezone
from decimal import Decimal
from unittest import TestCase

from agent_platform.quotas.service import UsageEvent
from agent_platform.value_management_metrics import (
    ToolCostLine,
    aggregate_value_costs,
    calculate_business_value_metrics,
)


def event(event_id, run_id="run-1", *, tenant="tenant-a", model="model-x",
          source="estimated", input_tokens=100, output_tokens=50,
          cost="0.25", tool_calls=2, price_card_version="price-v1"):
    return UsageEvent(
        event_id=event_id, tenant_id=tenant, run_id=run_id, model_key=model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cost_usd=Decimal(cost), occurred_at=datetime(2026, 11, 22, tzinfo=timezone.utc),
        tool_calls=tool_calls, source=source, price_card_version=price_card_version,
    )


class ValueManagementMetricsTests(TestCase):
    def test_cycle_time_and_throughput_metrics_use_explicit_denominators(self):
        metrics = calculate_business_value_metrics(
            completed_units=20,
            baseline_cycle_minutes_per_unit=Decimal("10"),
            actual_cycle_minutes_per_unit=Decimal("7"),
            baseline_throughput_per_hour=Decimal("6"),
            actual_throughput_per_hour=Decimal("8"),
        )
        self.assertEqual(metrics.cycle_time_delta_minutes_per_unit, Decimal("3"))
        self.assertEqual(metrics.cycle_time_reduction_rate, Decimal("0.3"))
        self.assertEqual(metrics.cycle_minutes_saved, Decimal("60"))
        self.assertEqual(metrics.throughput_delta_per_hour, Decimal("2"))
        self.assertEqual(metrics.throughput_change_rate, Decimal(2) / Decimal(6))

    def test_negative_performance_change_is_preserved_not_clamped(self):
        metrics = calculate_business_value_metrics(
            completed_units=3,
            baseline_cycle_minutes_per_unit=Decimal("5"),
            actual_cycle_minutes_per_unit=Decimal("8"),
            baseline_throughput_per_hour=Decimal("10"),
            actual_throughput_per_hour=Decimal("7"),
        )
        self.assertEqual(metrics.cycle_time_delta_minutes_per_unit, Decimal("-3"))
        self.assertEqual(metrics.cycle_minutes_saved, Decimal("-9"))
        self.assertEqual(metrics.throughput_change_rate, Decimal("-0.3"))

    def test_usage_costs_aggregate_by_model_and_source_without_fake_tool_prices(self):
        result = aggregate_value_costs(
            [event("e1"), event("e2", model="model-y", source="provider_reported", cost="0.5")],
            tenant_id="tenant-a", run_ids=["run-1"],
        )
        self.assertEqual(result["input_tokens"], 200)
        self.assertEqual(result["output_tokens"], 100)
        self.assertEqual(result["tool_calls"], 4)
        self.assertEqual(result["model_cost_usd"], "0.75")
        self.assertIsNone(result["tool_cost_usd"])
        self.assertIsNone(result["total_ai_cost_usd"])
        self.assertEqual(result["cost_status"], "tool_cost_missing")
        self.assertEqual(
            result["model_cost_by_source_usd"],
            {"estimated": "0.25", "provider_reported": "0.5"},
        )

    def test_tool_costs_are_counted_only_when_explicitly_supplied(self):
        result = aggregate_value_costs(
            [event("e1")], tenant_id="tenant-a", run_ids=["run-1"],
            tool_cost_lines=[
                ToolCostLine("crm.lookup", 2, Decimal("0.1"), "reconciled", "v2", "invoice-line-1")
            ],
        )
        self.assertEqual(result["tool_cost_usd"], "0.1")
        self.assertEqual(result["total_ai_cost_usd"], "0.35")
        self.assertEqual(result["tool_cost_by_key"]["crm.lookup"]["pricing_versions"], ["v2"])
        self.assertEqual(result["tool_cost_by_key"]["crm.lookup"]["evidence_refs"], ["invoice-line-1"])

    def test_usage_tenant_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the authenticated tenant"):
            aggregate_value_costs(
                [event("e1", tenant="tenant-b")], tenant_id="tenant-a", run_ids=["run-1"]
            )

    def test_usage_run_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the requested Runs"):
            aggregate_value_costs(
                [event("e1", run_id="run-other")], tenant_id="tenant-a", run_ids=["run-1"]
            )

    def test_invalid_cycle_baseline_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "positive"):
            calculate_business_value_metrics(
                completed_units=1, baseline_cycle_minutes_per_unit=Decimal("0"),
                actual_cycle_minutes_per_unit=Decimal("1"),
                baseline_throughput_per_hour=Decimal("1"),
                actual_throughput_per_hour=Decimal("1"),
            )

    def test_partial_tool_cost_evidence_does_not_claim_total_cost(self):
        result = aggregate_value_costs(
            [event("e1")], tenant_id="tenant-a", run_ids=["run-1"],
            tool_cost_lines=[ToolCostLine("crm.lookup", 1, Decimal("0.1"), "estimated", "v2")],
        )
        self.assertEqual(result["tool_calls"], 2)
        self.assertIsNone(result["tool_cost_usd"])
        self.assertIsNone(result["total_ai_cost_usd"])
        self.assertEqual(result["tool_cost_status"], "partial_or_mismatched")

    def test_zero_tool_calls_have_no_unpriced_tool_cost_gap(self):
        result = aggregate_value_costs(
            [event("e1", tool_calls=0)], tenant_id="tenant-a", run_ids=["run-1"],
        )
        self.assertEqual(result["tool_cost_usd"], "0")
        self.assertEqual(result["total_ai_cost_usd"], "0.25")
        self.assertEqual(result["tool_cost_status"], "not_applicable")

    def test_empty_usage_ledger_does_not_infer_zero_model_or_tool_cost(self):
        result = aggregate_value_costs([], tenant_id="tenant-a", run_ids=["run-1"])
        self.assertIsNone(result["model_cost_usd"])
        self.assertIsNone(result["tool_cost_usd"])
        self.assertIsNone(result["total_ai_cost_usd"])
        self.assertEqual(result["cost_status"], "model_usage_missing")
        self.assertEqual(result["tool_cost_status"], "usage_missing")

    def test_estimated_model_cost_without_price_card_is_unknown_not_zero(self):
        result = aggregate_value_costs(
            [event("unpriced", source="estimated", cost="0", model="unknown-model",
                   price_card_version=None)],
            tenant_id="tenant-a", run_ids=["run-1"], tool_cost_lines=[],
        )
        self.assertIsNone(result["model_cost_usd"])
        self.assertIsNone(result["model_cost_by_key_usd"]["unknown-model"])
        self.assertEqual(result["unpriced_model_events"], 1)
        self.assertIsNone(result["total_ai_cost_usd"])
        self.assertEqual(result["cost_status"], "model_cost_incomplete")
