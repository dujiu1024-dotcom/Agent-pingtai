"""Evidence-based AI value attribution and ROI calculations.

The calculator deliberately excludes agent elapsed time from labor savings.
Only verified manual baseline time, actual human touch time and rework
are eligible. Missing financial evidence remains unknown (None), never zero.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def money(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid decimal") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{field} must be finite and non-negative")
    return result


@dataclass(frozen=True)
class HumanBaseline:
    tenant_id: str
    process_id: str
    role_id: str
    manual_minutes_per_unit: Decimal
    labor_cost_per_hour: Decimal
    quality_rate: Decimal
    sample_size: int
    source: str
    owner_id: str
    version: str = "1"

    def __post_init__(self) -> None:
        if not all((self.tenant_id, self.process_id, self.role_id, self.source, self.owner_id, self.version)):
            raise ValueError("baseline identifiers, source and owner are required")
        if self.manual_minutes_per_unit <= 0 or self.labor_cost_per_hour < 0:
            raise ValueError("baseline time must be positive and labor cost non-negative")
        if not Decimal("0") <= self.quality_rate <= Decimal("1"):
            raise ValueError("quality_rate must be between 0 and 1")
        if self.sample_size < 1:
            raise ValueError("sample_size must be positive")


@dataclass(frozen=True)
class AIContribution:
    units_total: int
    auto_completed: int
    assisted_completed: int
    human_takeovers: int
    rework_units: int
    failed_units: int
    actual_human_minutes: Decimal
    rework_human_minutes: Decimal
    agent_run_minutes: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        counts = (self.units_total, self.auto_completed, self.assisted_completed,
                  self.human_takeovers, self.rework_units, self.failed_units)
        if any(not isinstance(v, int) or isinstance(v, bool) or v < 0 for v in counts):
            raise ValueError("contribution counts must be non-negative integers")
        if self.units_total <= 0:
            raise ValueError("units_total must be positive")
        if self.auto_completed + self.assisted_completed + self.human_takeovers + self.failed_units > self.units_total:
            raise ValueError("classified contribution units cannot exceed units_total")
        for name in ("actual_human_minutes", "rework_human_minutes", "agent_run_minutes"):
            value = getattr(self, name)
            if value < 0:
                raise ValueError(f"{name} must be non-negative")


@dataclass(frozen=True)
class ValueResult:
    eligible_baseline_minutes: Decimal
    actual_human_minutes: Decimal
    rework_minutes: Decimal
    saved_minutes: Decimal
    saved_hours: Decimal
    labor_savings: Decimal
    other_business_benefit: Decimal | None
    ai_cost: Decimal | None
    net_benefit: Decimal | None
    roi: Decimal | None
    financial_status: str
    attribution_version: str
    cost_basis: str


def calculate_value(
    baseline: HumanBaseline,
    contribution: AIContribution,
    *,
    other_business_benefit: Decimal | None = None,
    ai_cost: Decimal | None = None,
    attribution_version: str = "1",
    cost_is_settled: bool = False,
    business_benefit_confirmed: bool = False,
) -> ValueResult:
    """Calculate conservative labor savings and evidence-gated financial ROI."""
    if not attribution_version:
        raise ValueError("attribution_version is required")
    eligible_units = contribution.auto_completed + contribution.assisted_completed + contribution.human_takeovers
    eligible = baseline.manual_minutes_per_unit * eligible_units
    actual = contribution.actual_human_minutes
    rework = contribution.rework_human_minutes
    saved = max(Decimal("0"), eligible - actual - rework)
    labor = saved / Decimal("60") * baseline.labor_cost_per_hour
    benefit = None if other_business_benefit is None else money(other_business_benefit, "other_business_benefit")
    cost = None if ai_cost is None else money(ai_cost, "ai_cost")
    # ROI is withheld unless both cost and benefit inputs have evidence.
    if cost is None or benefit is None:
        net, roi, status = None, None, "estimated"
    else:
        net = labor + benefit - cost
        roi = None if cost == 0 else net / cost
        status = ("financially_confirmed" if cost_is_settled and business_benefit_confirmed else "estimated")
    return ValueResult(
        eligible_baseline_minutes=eligible, actual_human_minutes=actual,
        rework_minutes=rework, saved_minutes=saved, saved_hours=saved / Decimal("60"),
        labor_savings=labor, other_business_benefit=benefit, ai_cost=cost,
        net_benefit=net, roi=roi, financial_status=status,
        attribution_version=attribution_version,
        cost_basis=("settled" if cost_is_settled else "estimated"),
    )
