"""Business-value metrics and cost association for AI value attribution.

This module consumes the existing #17 usage ledger. It never estimates tool
charges from call counts and never treats absent cost evidence as zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

from agent_platform.quotas.service import UsageEvent


def _decimal(value: Any, field: str, *, allow_zero: bool = True) -> Decimal:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid decimal") from exc
    if not amount.is_finite() or amount < 0 or (not allow_zero and amount == 0):
        qualifier = "positive" if not allow_zero else "non-negative"
        raise ValueError(f"{field} must be finite and {qualifier}")
    return amount


@dataclass(frozen=True)
class BusinessValueMetrics:
    completed_units: int
    baseline_cycle_minutes_per_unit: Decimal
    actual_cycle_minutes_per_unit: Decimal
    baseline_throughput_per_hour: Decimal
    actual_throughput_per_hour: Decimal
    cycle_time_delta_minutes_per_unit: Decimal
    cycle_time_reduction_rate: Decimal
    cycle_minutes_saved: Decimal
    throughput_delta_per_hour: Decimal
    throughput_change_rate: Decimal


def calculate_business_value_metrics(
    *,
    completed_units: int,
    baseline_cycle_minutes_per_unit: Decimal,
    actual_cycle_minutes_per_unit: Decimal,
    baseline_throughput_per_hour: Decimal,
    actual_throughput_per_hour: Decimal,
) -> BusinessValueMetrics:
    """Calculate cycle-time and throughput deltas using explicit denominators."""
    if isinstance(completed_units, bool) or not isinstance(completed_units, int) or completed_units < 0:
        raise ValueError("completed_units must be a non-negative integer")
    baseline_cycle = _decimal(baseline_cycle_minutes_per_unit, "baseline_cycle_minutes_per_unit", allow_zero=False)
    actual_cycle = _decimal(actual_cycle_minutes_per_unit, "actual_cycle_minutes_per_unit")
    baseline_throughput = _decimal(baseline_throughput_per_hour, "baseline_throughput_per_hour", allow_zero=False)
    actual_throughput = _decimal(actual_throughput_per_hour, "actual_throughput_per_hour")
    cycle_delta = baseline_cycle - actual_cycle
    throughput_delta = actual_throughput - baseline_throughput
    return BusinessValueMetrics(
        completed_units=completed_units,
        baseline_cycle_minutes_per_unit=baseline_cycle,
        actual_cycle_minutes_per_unit=actual_cycle,
        baseline_throughput_per_hour=baseline_throughput,
        actual_throughput_per_hour=actual_throughput,
        cycle_time_delta_minutes_per_unit=cycle_delta,
        cycle_time_reduction_rate=cycle_delta / baseline_cycle,
        cycle_minutes_saved=cycle_delta * completed_units,
        throughput_delta_per_hour=throughput_delta,
        throughput_change_rate=throughput_delta / baseline_throughput,
    )


@dataclass(frozen=True)
class ToolCostLine:
    tool_key: str
    call_count: int
    cost_usd: Decimal
    source: str
    pricing_version: str
    evidence_ref: str | None = None

    def __post_init__(self) -> None:
        if (
            not isinstance(self.tool_key, str) or not self.tool_key.strip()
            or not isinstance(self.pricing_version, str) or not self.pricing_version.strip()
        ):
            raise ValueError("tool_key and pricing_version are required")
        if self.source in {"provider_reported", "reconciled"} and not (self.evidence_ref or "").strip():
            raise ValueError("provider-reported or reconciled tool cost requires evidence_ref")
        if isinstance(self.call_count, bool) or not isinstance(self.call_count, int) or self.call_count < 1:
            raise ValueError("tool cost call_count must be a positive integer")
        object.__setattr__(self, "cost_usd", _decimal(self.cost_usd, "tool cost"))
        if self.source not in {"estimated", "provider_reported", "reconciled"}:
            raise ValueError("tool cost source must be estimated, provider_reported or reconciled")


def aggregate_value_costs(
    events: Iterable[UsageEvent],
    *,
    tenant_id: str,
    run_ids: Iterable[str],
    tool_cost_lines: Iterable[ToolCostLine] | None = None,
    usage_truncated: bool = False,
) -> dict[str, Any]:
    """Aggregate existing model usage ledger rows for the exact tenant and Runs."""
    requested = tuple(dict.fromkeys(run_ids))
    if not tenant_id or not requested or any(not item for item in requested):
        raise ValueError("tenant_id and at least one non-empty run_id are required")
    requested_set = set(requested)
    selected: dict[str, UsageEvent] = {}
    for event in events:
        if not isinstance(event, UsageEvent):
            raise TypeError("events must contain UsageEvent instances")
        if event.tenant_id != tenant_id:
            raise ValueError("usage ledger returned an event outside the authenticated tenant")
        if event.run_id not in requested_set:
            raise ValueError("usage ledger returned an event outside the requested Runs")
        previous = selected.get(event.event_id)
        if previous is not None and previous != event:
            raise ValueError("duplicate usage event id has conflicting values")
        selected[event.event_id] = event

    model_costs: dict[str, Decimal] = {}
    source_costs: dict[str, Decimal] = {}
    model_tokens: dict[str, dict[str, Any]] = {}
    unpriced_models: set[str] = set()
    total_input = total_output = tool_calls = 0
    for event in selected.values():
        if event.source == "estimated" and event.price_card_version is None:
            unpriced_models.add(event.model_key)
        else:
            model_costs[event.model_key] = model_costs.get(event.model_key, Decimal("0")) + event.cost_usd
            source_costs[event.source] = source_costs.get(event.source, Decimal("0")) + event.cost_usd
        tokens = model_tokens.setdefault(event.model_key, {
            "input_tokens": 0, "output_tokens": 0, "events": 0, "pricing_versions": [],
        })
        tokens["input_tokens"] += event.input_tokens
        tokens["output_tokens"] += event.output_tokens
        tokens["events"] += 1
        if event.price_card_version and event.price_card_version not in tokens["pricing_versions"]:
            tokens["pricing_versions"].append(event.price_card_version)
        total_input += event.input_tokens
        total_output += event.output_tokens
        tool_calls += event.tool_calls

    tool_lines = tuple(tool_cost_lines) if tool_cost_lines is not None else None
    tool_cost: Decimal | None = None
    tool_breakdown: dict[str, dict[str, Any]] | None = None
    tool_line_calls = 0
    if tool_lines is not None:
        raw_breakdown: dict[str, dict[str, Any]] = {}
        for line in tool_lines:
            if not isinstance(line, ToolCostLine):
                raise TypeError("tool_cost_lines must contain ToolCostLine instances")
            tool_line_calls += line.call_count
            item = raw_breakdown.setdefault(line.tool_key, {
                "calls": 0, "cost_usd": Decimal("0"), "sources": {}, "pricing_versions": [],
                "evidence_refs": [],
            })
            item["calls"] += line.call_count
            item["cost_usd"] += line.cost_usd
            item["sources"][line.source] = item["sources"].get(line.source, Decimal("0")) + line.cost_usd
            if line.pricing_version not in item["pricing_versions"]:
                item["pricing_versions"].append(line.pricing_version)
            if line.evidence_ref and line.evidence_ref not in item["evidence_refs"]:
                item["evidence_refs"].append(line.evidence_ref)
        tool_breakdown = {
            key: {
                "calls": value["calls"], "cost_usd": str(value["cost_usd"]),
                "sources": {k: str(v) for k, v in sorted(value["sources"].items())},
                "pricing_versions": sorted(value["pricing_versions"]),
                "evidence_refs": sorted(value["evidence_refs"]),
            }
            for key, value in sorted(raw_breakdown.items())
        }
        if (
            not usage_truncated and tool_line_calls == tool_calls
            and (tool_lines or (tool_calls == 0 and selected))
        ):
            tool_cost = sum((line.cost_usd for line in tool_lines), Decimal("0"))
    elif tool_calls == 0 and selected and not usage_truncated:
        tool_cost = Decimal("0")

    model_total = (
        sum(model_costs.values(), Decimal("0"))
        if selected and not unpriced_models and not usage_truncated else None
    )
    total_cost = model_total + tool_cost if model_total is not None and tool_cost is not None else None
    return {
        "tenant_id": tenant_id,
        "run_ids": list(requested),
        "usage_event_count": len(selected),
        "usage_truncated": usage_truncated,
        "usage_event_ids": sorted(selected),
        "missing_run_ids": sorted(requested_set - {event.run_id for event in selected.values()}),
        "input_tokens": total_input,
        "output_tokens": total_output,
        "total_tokens": total_input + total_output,
        "tool_calls": tool_calls,
        "model_cost_usd": str(model_total) if model_total is not None else None,
        "tool_cost_usd": str(tool_cost) if tool_cost is not None else None,
        "total_ai_cost_usd": str(total_cost) if total_cost is not None else None,
        "model_cost_by_key_usd": {
            key: (
                None if key in unpriced_models or usage_truncated
                else str(model_costs.get(key, Decimal("0")))
            )
            for key in sorted(set(model_costs) | unpriced_models)
        },
        "unpriced_model_events": sum(
            1 for event in selected.values()
            if event.source == "estimated" and event.price_card_version is None
        ),
        "model_usage": {
            key: {**value, "pricing_versions": sorted(value["pricing_versions"])}
            for key, value in sorted(model_tokens.items())
        },
        "model_cost_by_source_usd": (
            {key: str(value) for key, value in sorted(source_costs.items())}
            if not unpriced_models and not usage_truncated else None
        ),
        "partial_model_cost_by_source_usd": (
            {key: str(value) for key, value in sorted(source_costs.items())}
            if unpriced_models or usage_truncated else None
        ),
        "tool_cost_by_key": tool_breakdown,
        "tool_cost_status": (
            "usage_missing" if not selected
            else "usage_truncated" if usage_truncated
            else "not_applicable" if tool_calls == 0 and not tool_lines
            else "not_priced" if tool_lines is None or not tool_lines
            else "provided" if tool_line_calls == tool_calls
            else "partial_or_mismatched"
        ),
        "cost_status": (
            "complete" if total_cost is not None
            else "model_usage_missing" if not selected
            else "usage_truncated" if usage_truncated
            else "model_cost_incomplete" if unpriced_models
            else "tool_cost_missing"
        ),
        "usage_sources": sorted(source_costs),
    }
