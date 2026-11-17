"""Tenant-scoped quota administration and operational alert endpoints."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.quotas.service import Budget, QuotaService


def create_quota_admin_router(
    *, service: QuotaService, authorize_manage: Callable[[Principal], Any],
    authorize_scope: Callable[[Principal, str, str], Any] | None = None,
    audit_writer: Any | None = None,
) -> APIRouter:
    """Expose read/update operations; mutations are audited before persistence."""
    router = APIRouter(prefix="/api/platform/v1/quotas", tags=["quotas"])

    def principal(request: Request) -> Principal:
        value = getattr(request.state, "identity.principal", None)
        if value is None:
            value = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(value, Principal):
            raise HTTPException(401, "authenticated user required")
        return value

    async def allowed(result: Any) -> bool:
        if inspect.isawaitable(result):
            result = await result
        return result is True

    async def authorize_resource(actor: Principal, scope_type: str, scope_id: str) -> None:
        if scope_type == "tenant":
            if scope_id != actor.tenant_id:
                raise HTTPException(403, "tenant quota scope must match authenticated tenant")
            return
        if authorize_scope is None or not await allowed(authorize_scope(actor, scope_type, scope_id)):
            raise HTTPException(403, "quota scope is not accessible")

    async def audit(actor: Principal, action: str, budget: Budget) -> None:
        if audit_writer is None:
            raise HTTPException(503, "quota audit writer is not configured")
        event = {
            "tenant_id": actor.tenant_id, "actor_id": actor.user_id,
            "action": action, "resource_type": "quota_budget",
            "resource_id": f"{budget.scope_type}:{budget.scope_id}",
            "period_start": budget.period_start.isoformat(),
            "period_end": budget.period_end.isoformat(),
            "limits": {
                "hard_tokens": budget.hard_tokens,
                "hard_cost_usd": str(budget.hard_cost_usd) if budget.hard_cost_usd is not None else None,
                "hard_concurrency": budget.hard_concurrency,
                "soft_tokens": budget.soft_tokens,
                "soft_cost_usd": str(budget.soft_cost_usd) if budget.soft_cost_usd is not None else None,
                "hard_input_tokens": budget.hard_input_tokens,
                "hard_output_tokens": budget.hard_output_tokens,
                "hard_context_window_tokens": budget.hard_context_window_tokens,
            },
        }
        writer = audit_writer
        method = getattr(writer, "write", None) or getattr(writer, "append", None)
        if method is None and callable(writer):
            method = writer
        if method is None:
            raise HTTPException(503, "quota audit writer is unavailable")
        try:
            result = method(event)
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            raise HTTPException(503, "quota audit write failed; change was not applied") from exc

    def serialize(budget: Budget) -> dict[str, Any]:
        return {
            "tenant_id": budget.tenant_id, "scope_type": budget.scope_type,
            "scope_id": budget.scope_id, "period_start": budget.period_start.isoformat(),
            "period_end": budget.period_end.isoformat(), "hard_tokens": budget.hard_tokens,
            "hard_cost_usd": str(budget.hard_cost_usd) if budget.hard_cost_usd is not None else None,
            "hard_concurrency": budget.hard_concurrency, "soft_tokens": budget.soft_tokens,
            "soft_cost_usd": str(budget.soft_cost_usd) if budget.soft_cost_usd is not None else None,
            "hard_input_tokens": budget.hard_input_tokens,
            "hard_output_tokens": budget.hard_output_tokens,
            "hard_context_window_tokens": budget.hard_context_window_tokens,
        }

    @router.get("/budgets/{scope_type}/{scope_id}")
    async def get_budget(request: Request, scope_type: str, scope_id: str, at: datetime | None = None):
        actor = principal(request)
        await authorize_resource(actor, scope_type, scope_id)
        instant = at or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise HTTPException(400, "at must be timezone-aware")
        store = service.store
        getter = getattr(store, "get_budget", None)
        if not callable(getter):
            raise HTTPException(503, "quota budget reader is unavailable")
        try:
            budget = await getter(actor.tenant_id, scope_type, scope_id, instant)
        except Exception as exc:
            raise HTTPException(503, "quota budget lookup failed") from exc
        if budget is None:
            raise HTTPException(404, "quota budget not found")
        return serialize(budget)

    @router.put("/budgets/{scope_type}/{scope_id}")
    async def put_budget(request: Request, scope_type: str, scope_id: str):
        actor = principal(request)
        if not await allowed(authorize_manage(actor)):
            raise HTTPException(403, "quota management permission required")
        await authorize_resource(actor, scope_type, scope_id)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            start = datetime.fromisoformat(str(body["period_start"]))
            end = datetime.fromisoformat(str(body["period_end"]))
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise ValueError("period timestamps must be ordered and timezone-aware")
            if end - start > __import__("datetime").timedelta(days=366):
                raise ValueError("quota period cannot exceed 366 days")
            def integer(name: str):
                value = body.get(name)
                if value is None:
                    return None
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{name} must be a non-negative integer")
                return value
            def money(name: str):
                value = body.get(name)
                if value is None:
                    return None
                result = Decimal(str(value))
                if not result.is_finite() or result < 0:
                    raise ValueError(f"{name} must be a finite non-negative amount")
                return result
            budget = Budget(
                tenant_id=actor.tenant_id, scope_type=scope_type, scope_id=scope_id,
                period_start=start, period_end=end,
                hard_tokens=integer("hard_tokens"), hard_cost_usd=money("hard_cost_usd"),
                hard_concurrency=integer("hard_concurrency"), soft_tokens=integer("soft_tokens"),
                soft_cost_usd=money("soft_cost_usd"),
                hard_input_tokens=integer("hard_input_tokens"),
                hard_output_tokens=integer("hard_output_tokens"),
                hard_context_window_tokens=integer("hard_context_window_tokens"),
            )
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise HTTPException(400, str(exc)) from exc
        await audit(actor, "quota.budget.updated", budget)
        try:
            await service.configure(budget)
        except Exception as exc:
            raise HTTPException(503, "quota budget persistence failed") from exc
        return serialize(budget)

    @router.get("/usage/current/{scope_type}/{scope_id}")
    async def current_usage(request: Request, scope_type: str, scope_id: str, at: datetime | None = None):
        actor = principal(request)
        await authorize_resource(actor, scope_type, scope_id)
        instant = at or datetime.now(timezone.utc)
        if instant.tzinfo is None:
            raise HTTPException(400, "at must be timezone-aware")
        try:
            budget = await service.store.get_budget(actor.tenant_id, scope_type, scope_id, instant)
            if budget is None:
                raise HTTPException(404, "quota budget not found")
            tokens, cost, concurrency = await service.store.usage(
                actor.tenant_id, scope_type, scope_id, budget.period_start, budget.period_end,
            )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "quota usage lookup failed") from exc
        return {
            "tenant_id": actor.tenant_id, "scope_type": scope_type, "scope_id": scope_id,
            "period_start": budget.period_start.isoformat(), "period_end": budget.period_end.isoformat(),
            "used_tokens": tokens, "used_cost_usd": str(cost), "active_concurrency": concurrency,
            "hard_tokens": budget.hard_tokens, "hard_cost_usd": str(budget.hard_cost_usd) if budget.hard_cost_usd is not None else None,
            "hard_concurrency": budget.hard_concurrency,
        }

    return router
