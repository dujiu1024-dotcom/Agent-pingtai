from decimal import Decimal

import pytest

from agent_platform.value_management import AIContribution, HumanBaseline, calculate_value


def baseline():
    return HumanBaseline(
        tenant_id="t1", process_id="invoice", role_id="clerk",
        manual_minutes_per_unit=Decimal("10"), labor_cost_per_hour=Decimal("60"),
        quality_rate=Decimal("0.98"), sample_size=100, source="time study 2026-11",
        owner_id="manager-1",
    )


def contribution(**overrides):
    values = dict(
        units_total=10, auto_completed=4, assisted_completed=3, human_takeovers=2,
        rework_units=1, failed_units=1, actual_human_minutes=Decimal("20"),
        rework_human_minutes=Decimal("10"), agent_run_minutes=Decimal("500"),
    )
    values.update(overrides)
    return AIContribution(**values)


def test_savings_uses_baseline_and_human_touch_and_rework_not_agent_runtime():
    result = calculate_value(baseline(), contribution())
    assert result.eligible_baseline_minutes == Decimal("90")
    assert result.saved_minutes == Decimal("60")
    assert result.saved_hours == Decimal("1")
    assert result.labor_savings == Decimal("60")
    assert result.ai_cost is None
    assert result.net_benefit is None
    assert result.roi is None
    assert result.financial_status == "estimated"
    assert result.cost_basis == "estimated"


def test_financial_roi_requires_both_cost_and_business_evidence():
    result = calculate_value(
        baseline(), contribution(), other_business_benefit=Decimal("40"),
        ai_cost=Decimal("20"), cost_is_settled=True, business_benefit_confirmed=True,
    )
    assert result.net_benefit == Decimal("80")
    assert result.roi == Decimal("4")
    assert result.financial_status == "financially_confirmed"
    assert result.cost_basis == "settled"


def test_missing_business_evidence_does_not_fake_zero_or_roi():
    result = calculate_value(baseline(), contribution(), ai_cost=Decimal("20"))
    assert result.other_business_benefit is None
    assert result.net_benefit is None
    assert result.roi is None


def test_rework_can_consume_all_savings_but_never_create_negative_saved_time():
    result = calculate_value(
        baseline(), contribution(actual_human_minutes=Decimal("80"),
                                 rework_human_minutes=Decimal("20")),
    )
    assert result.saved_minutes == 0
    assert result.labor_savings == 0


def test_agent_runtime_is_never_added_to_human_time():
    short = calculate_value(baseline(), contribution(agent_run_minutes=Decimal("1")))
    long = calculate_value(baseline(), contribution(agent_run_minutes=Decimal("99999")))
    assert short.saved_minutes == long.saved_minutes


def test_invalid_classification_is_rejected():
    with pytest.raises(ValueError, match="cannot exceed"):
        contribution(auto_completed=10, assisted_completed=3)


def test_missing_baseline_provenance_is_rejected():
    with pytest.raises(ValueError, match="identifiers"):
        HumanBaseline(
            tenant_id="t1", process_id="invoice", role_id="clerk",
            manual_minutes_per_unit=Decimal("10"), labor_cost_per_hour=Decimal("60"),
            quality_rate=Decimal("0.98"), sample_size=100, source="", owner_id="mgr",
        )


def test_estimated_cost_and_unconfirmed_benefit_never_claim_financial_confirmation():
    result = calculate_value(
        baseline(), contribution(), other_business_benefit=Decimal("40"),
        ai_cost=Decimal("20"), cost_is_settled=False, business_benefit_confirmed=False,
    )
    assert result.net_benefit == Decimal("80")
    assert result.roi == Decimal("4")
    assert result.financial_status == "estimated"
