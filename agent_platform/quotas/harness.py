"""Harness decorator that reserves tenant quota before execution and settles actual usage."""

from __future__ import annotations

from decimal import Decimal
from time import monotonic
from typing import Any, Callable

from agent_platform.quotas.service import QuotaService


class QuotaEnforcedHarnessAdapter:
    """Enforce server-side quota around every call made through this Harness adapter.

    Both estimators are mandatory: silently defaulting to zero would undermine
    hard budgets. Register this decorator in the trusted Harness composition root.
    """

    def __init__(
        self,
        inner: Any,
        *,
        quota_service: QuotaService,
        estimate_request: Callable[[Any], tuple[int, Decimal]],
        estimate_actual_cost: Callable[[Any, Any], Decimal],
        scope_resolver: Callable[[Any], tuple[str, str]] | None = None,
    ) -> None:
        self.inner = inner
        self.quota_service = quota_service
        self.estimate_request = estimate_request
        self.estimate_actual_cost = estimate_actual_cost
        self.scope_resolver = scope_resolver or self._tenant_scope

    @staticmethod
    def _tenant_scope(request: Any) -> tuple[str, str]:
        context = request.context
        return "tenant", context.tenant_id

    async def run(self, request: Any) -> Any:
        context = request.context
        scope_type, scope_id = self.scope_resolver(request)
        if scope_type == "tenant" and scope_id != context.tenant_id:
            raise ValueError("tenant quota scope must match authenticated Run tenant")
        estimated_tokens, estimated_cost = self.estimate_request(request)
        if not isinstance(estimated_tokens, int) or isinstance(estimated_tokens, bool) or estimated_tokens < 0:
            raise ValueError("estimated tokens must be a non-negative integer")
        if not isinstance(estimated_cost, Decimal) or not estimated_cost.is_finite() or estimated_cost < 0:
            raise ValueError("estimated cost must be a finite non-negative Decimal")
        reservation, _warnings = await self.quota_service.reserve_run(
            tenant_id=context.tenant_id,
            run_id=context.run_id,
            scope_type=scope_type,
            scope_id=scope_id,
            estimated_tokens=estimated_tokens,
            estimated_cost_usd=estimated_cost,
        )
        started = monotonic()
        try:
            result = await self.inner.run(request)
            usage = getattr(result, "usage", {}) or {}
            input_tokens = int(usage.get("input_tokens", 0))
            output_tokens = int(usage.get("output_tokens", 0))
            tool_calls = len(getattr(result, "tool_calls", ()) or ())
            actual_cost = self.estimate_actual_cost(request, result)
            if not isinstance(actual_cost, Decimal) or not actual_cost.is_finite() or actual_cost < 0:
                raise ValueError("actual cost must be a finite non-negative Decimal")
            if min(input_tokens, output_tokens, tool_calls) < 0:
                raise ValueError("actual usage counters must be non-negative")
            model_key = (
                f"{context.model_template.template_id}@{context.model_template.version}"
            )
            await self.quota_service.settle_run(
                reservation,
                model_key=model_key,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=actual_cost,
                project_id=context.project_id,
                agent_id=context.agent_id,
                tool_calls=tool_calls,
                runtime_ms=int((monotonic() - started) * 1000),
            )
            return result
        except BaseException:
            await self.quota_service.release_run(reservation)
            raise
