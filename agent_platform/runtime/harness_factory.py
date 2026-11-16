"""Canonical runtime composition for supervised, quota-enforced Harness execution."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

from agent_platform.quotas.harness import QuotaEnforcedHarnessAdapter
from agent_platform.quotas.service import QuotaService


def compose_quota_enforced_harness(
    inner: Any, *, quota_service: QuotaService,
    estimate_request: Callable[[Any], tuple[int, Decimal]],
    estimate_actual_cost: Callable[[Any, Any], Decimal],
    scope_resolver: Callable[[Any], tuple[str, str]] | None = None,
) -> QuotaEnforcedHarnessAdapter:
    """Wrap a provider Harness before exposing it to the Run execution path."""
    if quota_service is None:
        raise ValueError("quota_service is mandatory for platform Harness composition")
    if not callable(estimate_request) or not callable(estimate_actual_cost):
        raise ValueError("request and actual-cost estimators are mandatory")
    return QuotaEnforcedHarnessAdapter(
        inner, quota_service=quota_service, estimate_request=estimate_request,
        estimate_actual_cost=estimate_actual_cost, scope_resolver=scope_resolver,
    )
