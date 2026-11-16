"""Harness decorator that reserves tenant quota before execution and settles actual usage."""

from __future__ import annotations

from decimal import Decimal
from time import monotonic
from typing import Any, Callable

from agent_platform.quotas.service import QuotaService


class QuotaEnforcedHarnessAdapter:
    """Enforce server-side quota around every call made through this Harness adapter.

    Request and model/provider cost estimators are mandatory: silently defaulting
    to zero would undermine hard budgets. Optional tool-cost and storage-byte
    callbacks extend the ledger to those dimensions. The model cost callback must
    exclude tool cost when the separate tool-cost callback is configured.
    """

    def __init__(
        self,
        inner: Any,
        *,
        quota_service: QuotaService,
        estimate_request: Callable[[Any], tuple[int, Decimal] | tuple[int, int, Decimal]],
        estimate_actual_cost: Callable[[Any, Any], Decimal],
        scope_resolver: Callable[[Any], tuple[str, str]] | None = None,
        context_window_tokens: int | Callable[[Any], int] = 8192,
        estimate_tool_cost_usd: Callable[[Any, Any], Decimal] | None = None,
        measure_storage_bytes: Callable[[Any, Any], int] | None = None,
    ) -> None:
        if not callable(context_window_tokens) and (
            not isinstance(context_window_tokens, int)
            or isinstance(context_window_tokens, bool)
            or context_window_tokens < 1
        ):
            raise ValueError("context_window_tokens must be a positive integer or resolver")
        self.context_window_tokens = context_window_tokens
        self.inner = inner
        self.quota_service = quota_service
        self.estimate_request = estimate_request
        self.estimate_actual_cost = estimate_actual_cost
        self.scope_resolver = scope_resolver or self._tenant_scope
        self.estimate_tool_cost_usd = estimate_tool_cost_usd
        self.measure_storage_bytes = measure_storage_bytes

    @staticmethod
    def _tenant_scope(request: Any) -> tuple[str, str]:
        context = request.context
        return "tenant", context.tenant_id

    @staticmethod
    def _reported_usage(result: Any) -> tuple[int, int]:
        """Require explicit provider usage; never turn missing accounting into zero."""
        usage = getattr(result, "usage", None)
        if not isinstance(usage, dict):
            raise ValueError("provider response is missing a usage mapping")
        counters: list[int] = []
        for key in ("input_tokens", "output_tokens"):
            value = usage.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"provider response has missing or invalid {key}")
            counters.append(value)
        return counters[0], counters[1]

    async def run(self, request: Any) -> Any:
        context = request.context
        scope_type, scope_id = self.scope_resolver(request)
        if scope_type == "tenant" and scope_id != context.tenant_id:
            raise ValueError("tenant quota scope must match authenticated Run tenant")
        estimate = self.estimate_request(request)
        if not isinstance(estimate, tuple) or len(estimate) not in {2, 3}:
            raise ValueError(
                "request estimator must return (total_tokens, cost) or "
                "(input_tokens, output_tokens, cost)"
            )
        if len(estimate) == 3:
            estimated_input_tokens, estimated_output_tokens, estimated_cost = estimate
            estimated_tokens = estimated_input_tokens + estimated_output_tokens
        else:
            estimated_tokens, estimated_cost = estimate
            estimated_input_tokens = None
            estimated_output_tokens = None
        if not isinstance(estimated_tokens, int) or isinstance(estimated_tokens, bool) or estimated_tokens < 0:
            raise ValueError("estimated tokens must be a non-negative integer")
        if not isinstance(estimated_cost, Decimal) or not estimated_cost.is_finite() or estimated_cost < 0:
            raise ValueError("estimated cost must be a finite non-negative Decimal")
        # The estimator must include the complete prompt plus the planned output
        # allowance. Reject rather than silently truncate instructions or evidence.
        context_window_tokens = (
            self.context_window_tokens(request)
            if callable(self.context_window_tokens)
            else self.context_window_tokens
        )
        if (
            not isinstance(context_window_tokens, int)
            or isinstance(context_window_tokens, bool)
            or context_window_tokens < 1
        ):
            raise ValueError("resolved model context window must be a positive integer")
        if estimated_tokens > context_window_tokens:
            from agent_platform.quotas.service import BudgetExceeded
            raise BudgetExceeded("estimated Run context exceeds the configured model context window")
        reservation, _warnings = await self.quota_service.reserve_run(
            tenant_id=context.tenant_id,
            run_id=context.run_id,
            scope_type=scope_type,
            scope_id=scope_id,
            estimated_tokens=estimated_tokens,
            estimated_cost_usd=estimated_cost,
            estimated_input_tokens=estimated_input_tokens,
            estimated_output_tokens=estimated_output_tokens,
        )
        started = monotonic()
        try:
            result = await self.inner.run(request)
        except BaseException:
            # No provider result exists, so releasing the reservation is safe.
            await self.quota_service.release_run(reservation)
            raise

        # The provider has returned successfully. Missing/invalid usage or a
        # settlement failure must retain the reservation for reconciliation;
        # defaulting absent counters to zero would silently underbill.
        input_tokens, output_tokens = self._reported_usage(result)
        tool_calls = len(getattr(result, "tool_calls", ()) or ())
        actual_cost = self.estimate_actual_cost(request, result)
        if not isinstance(actual_cost, Decimal) or not actual_cost.is_finite() or actual_cost < 0:
            raise ValueError("actual cost must be a finite non-negative Decimal")
        if self.estimate_tool_cost_usd is not None:
            tool_cost = self.estimate_tool_cost_usd(request, result)
            if not isinstance(tool_cost, Decimal) or not tool_cost.is_finite() or tool_cost < 0:
                raise ValueError("tool cost must be a finite non-negative Decimal")
            actual_cost += tool_cost
        storage_bytes = (
            self.measure_storage_bytes(request, result)
            if self.measure_storage_bytes is not None else 0
        )
        if (
            not isinstance(storage_bytes, int) or isinstance(storage_bytes, bool)
            or storage_bytes < 0
        ):
            raise ValueError("measured storage bytes must be a non-negative integer")
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
            storage_bytes=storage_bytes,
        )
        return result
