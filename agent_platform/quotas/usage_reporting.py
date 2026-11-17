"""Deterministic cost and resource usage aggregation for tenant reports."""
from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from typing import Any, Iterable, cast

from agent_platform.quotas.service import UsageEvent


def build_usage_report(events: Iterable[UsageEvent]) -> dict[str, object]:
    """Aggregate ledger events without mixing estimates with provider actuals.

    The ledger is the source of truth for model spend. Tool calls and storage are
    reported as separate resource counters because their prices may be supplied by
    independent billing systems and must not be fabricated from model token prices.
    """
    events = tuple(events)
    totals = {
        "events": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "tool_calls": 0,
        "runtime_ms": 0,
        "storage_bytes": 0,
    }
    cost_by_source: dict[str, Decimal] = defaultdict(Decimal)
    by_model: dict[str, dict[str, object]] = {}
    by_project: dict[str, dict[str, object]] = {}
    by_agent: dict[str, dict[str, object]] = {}

    def add_dimension(target: dict[str, dict[str, object]], key: str, event: UsageEvent) -> None:
        item = target.setdefault(key, {
            "events": 0, "input_tokens": 0, "output_tokens": 0,
            "tool_calls": 0, "runtime_ms": 0, "storage_bytes": 0,
            "cost_usd": Decimal("0"),
        })
        item["events"] = int(cast(Any, item["events"])) + 1
        for field in ("input_tokens", "output_tokens", "tool_calls", "runtime_ms", "storage_bytes"):
            item[field] = int(cast(Any, item[field])) + int(getattr(event, field))
        item["cost_usd"] = Decimal(cast(Any, item["cost_usd"])) + event.cost_usd

    for event in events:
        if not isinstance(event, UsageEvent):
            raise TypeError("events must contain UsageEvent instances")
        totals["events"] += 1
        for field in ("input_tokens", "output_tokens", "tool_calls", "runtime_ms", "storage_bytes"):
            totals[field] += int(getattr(event, field))
        cost_by_source[event.source] += event.cost_usd
        add_dimension(by_model, event.model_key, event)
        if event.project_id:
            add_dimension(by_project, event.project_id, event)
        if event.agent_id:
            add_dimension(by_agent, event.agent_id, event)

    estimated = cost_by_source["estimated"]
    provider_actual = cost_by_source["provider_reported"]
    reconciled = cost_by_source["reconciled"]
    return {
        **totals,
        "total_tokens": totals["input_tokens"] + totals["output_tokens"],
        "cost_by_source_usd": {key: str(value) for key, value in sorted(cost_by_source.items())},
        "estimated_cost_usd": str(estimated),
        "provider_reported_cost_usd": str(provider_actual),
        "reconciled_event_cost_usd": str(reconciled),
        "estimated_vs_provider_actual_variance_usd": str(provider_actual - estimated),
        "by_model": {
            key: {**value, "cost_usd": str(value["cost_usd"])}
            for key, value in sorted(by_model.items())
        },
        "by_project": {
            key: {**value, "cost_usd": str(value["cost_usd"])}
            for key, value in sorted(by_project.items())
        },
        "by_agent": {
            key: {**value, "cost_usd": str(value["cost_usd"])}
            for key, value in sorted(by_agent.items())
        },
        "price_card_versions": sorted({
            event.price_card_version for event in events
            if event.price_card_version is not None
        }),
        "tool_and_storage_pricing": "not_included; counters are reported separately",
    }
