"""Tenant-scoped usage and cost reporting API."""
from __future__ import annotations

import csv
import inspect
from datetime import datetime, timedelta
from decimal import Decimal
from io import StringIO
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agent_platform.identity.models import Principal
from agent_platform.quotas.service import Budget, QuotaService
from agent_platform.quotas.usage_reporting import build_usage_report


def create_quota_reporting_router(*, quota_store: Any, quota_service: QuotaService | None = None, authorize_manage: Any | None = None, authorize_scope: Any | None = None, audit_writer: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/quotas", tags=["quotas"])

    def actor(request: Request) -> Principal:
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            # 兼容直接挂载 request.state.identity 的宿主（如单测/自建中间件）
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        return principal

    async def rows(principal: Principal, start: datetime, end: datetime,
                   run_id: str | None, model_key: str | None, limit: int):
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise HTTPException(400, "start and end must be ordered timezone-aware timestamps")
        if end - start > timedelta(days=366):
            raise HTTPException(400, "report range cannot exceed 366 days")
        if not 1 <= limit <= 10000:
            raise HTTPException(400, "limit must be between 1 and 10000")
        return await quota_store.list_usage(
            principal.tenant_id, start, end,
            run_id=run_id, model_key=model_key, limit=limit,
        )

    @router.post("/budgets", status_code=201)
    async def configure_budget(request: Request):
        principal = actor(request)
        if quota_service is None or authorize_manage is None:
            raise HTTPException(503, "quota administration is not configured")
        allowed = authorize_manage(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "quota management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            scope_type = str(body.get("scope_type") or "")
            scope_id = str(body.get("scope_id") or "")
            if scope_type == "tenant":
                if scope_id != principal.tenant_id:
                    raise PermissionError("tenant budget scope must match authenticated tenant")
            elif scope_type in {"project", "agent", "run", "model", "queue", "worker_pool"}:
                if authorize_scope is None:
                    raise PermissionError("resource scope authorization is not configured")
                scope_allowed = authorize_scope(principal, scope_type, scope_id)
                if inspect.isawaitable(scope_allowed):
                    scope_allowed = await scope_allowed
                if scope_allowed is not True:
                    raise PermissionError("quota scope is not accessible")
            else:
                raise ValueError("unsupported quota scope")
            def optional_decimal(name: str):
                value = body.get(name)
                if value is None:
                    return None
                amount = Decimal(str(value))
                if not amount.is_finite() or amount < 0:
                    raise ValueError(f"{name} must be a finite non-negative amount")
                return amount
            def optional_integer(name: str):
                value = body.get(name)
                if value is None:
                    return None
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise ValueError(f"{name} must be a non-negative integer")
                return value
            budget = Budget(
                tenant_id=principal.tenant_id, scope_type=scope_type, scope_id=scope_id,
                period_start=datetime.fromisoformat(str(body["period_start"])),
                period_end=datetime.fromisoformat(str(body["period_end"])),
                hard_tokens=optional_integer("hard_tokens"),
                hard_cost_usd=optional_decimal("hard_cost_usd"),
                hard_concurrency=optional_integer("hard_concurrency"),
                soft_tokens=optional_integer("soft_tokens"),
                soft_cost_usd=optional_decimal("soft_cost_usd"),
                hard_input_tokens=optional_integer("hard_input_tokens"),
                hard_output_tokens=optional_integer("hard_output_tokens"),
                hard_context_window_tokens=optional_integer("hard_context_window_tokens"),
            )
            if audit_writer is None:
                raise HTTPException(503, "quota audit writer is not configured")
            writer = getattr(audit_writer, "write", None) or getattr(audit_writer, "append", None)
            if writer is None and callable(audit_writer):
                writer = audit_writer
            if writer is None:
                raise HTTPException(503, "quota audit writer is unavailable")
            event = {
                "tenant_id": principal.tenant_id, "actor_id": principal.user_id,
                "action": "quota.budget.updated", "resource_type": "quota_budget",
                "resource_id": f"{budget.scope_type}:{budget.scope_id}",
                "period_start": budget.period_start.isoformat(),
                "period_end": budget.period_end.isoformat(),
                "hard_tokens": budget.hard_tokens,
                "hard_cost_usd": str(budget.hard_cost_usd) if budget.hard_cost_usd is not None else None,
                "hard_concurrency": budget.hard_concurrency,
                "soft_tokens": budget.soft_tokens,
                "soft_cost_usd": str(budget.soft_cost_usd) if budget.soft_cost_usd is not None else None,
            }
            try:
                audit_result = writer(event)
                if inspect.isawaitable(audit_result):
                    await audit_result
            except Exception as exc:
                raise HTTPException(503, "quota audit write failed; change was not applied") from exc
            await quota_service.configure(budget)
        except HTTPException:
            raise
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tenant_id": budget.tenant_id, "scope_type": budget.scope_type,
                "scope_id": budget.scope_id, "period_start": budget.period_start.isoformat(),
                "period_end": budget.period_end.isoformat(), "configured": True,
                "hard_input_tokens": budget.hard_input_tokens,
                "hard_output_tokens": budget.hard_output_tokens,
                "hard_context_window_tokens": budget.hard_context_window_tokens}


    @router.post("/reconciliation", status_code=201)
    async def reconcile_cost(request: Request):
        principal = actor(request)
        if authorize_manage is None or not callable(getattr(quota_store, "reconcile_cost", None)):
            raise HTTPException(503, "cost reconciliation is not configured")
        allowed = authorize_manage(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "quota management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            start = datetime.fromisoformat(str(body["period_start"]))
            end = datetime.fromisoformat(str(body["period_end"]))
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise ValueError("period_start and period_end must be ordered timezone-aware timestamps")
            if end - start > timedelta(days=366):
                raise ValueError("reconciliation range cannot exceed 366 days")
            source = body.get("source")
            if not isinstance(source, str) or not source.strip() or len(source) > 128:
                raise ValueError("source must be a non-empty label of at most 128 characters")
            actual_cost = Decimal(str(body["actual_cost_usd"]))
            if not actual_cost.is_finite() or actual_cost < 0:
                raise ValueError("actual_cost_usd must be a finite non-negative amount")
            return await quota_store.reconcile_cost(
                principal.tenant_id, start, end, source=source.strip(),
                actual_cost_usd=actual_cost,
            )
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise HTTPException(400, str(exc)) from exc


    @router.get("/usage")
    async def usage_report(request: Request, start: datetime, end: datetime,
                           run_id: str | None = None, model_key: str | None = None,
                           limit: int = 1000):
        principal = actor(request)
        events = await rows(principal, start, end, run_id, model_key, limit)
        return {"tenant_id": principal.tenant_id, "items": [{
            "event_id": event.event_id, "run_id": event.run_id,
            "model_key": event.model_key, "input_tokens": event.input_tokens,
            "output_tokens": event.output_tokens, "total_tokens": event.total_tokens,
            "cost_usd": str(event.cost_usd), "project_id": event.project_id,
            "agent_id": event.agent_id, "tool_calls": event.tool_calls,
            "runtime_ms": event.runtime_ms, "storage_bytes": event.storage_bytes,
            "price_card_version": event.price_card_version,
            "source": event.source, "occurred_at": event.occurred_at.isoformat(),
        } for event in events]}

    @router.get("/usage/summary")
    async def usage_summary(request: Request, start: datetime, end: datetime,
                            run_id: str | None = None, model_key: str | None = None,
                            limit: int = 10000):
        """Return tenant-isolated totals and cost variance, grouped by attribution."""
        principal = actor(request)
        events = await rows(principal, start, end, run_id, model_key, limit)
        return {
            "tenant_id": principal.tenant_id,
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            **build_usage_report(events),
        }

    @router.get("/usage.csv", response_class=PlainTextResponse)
    async def usage_csv(request: Request, start: datetime, end: datetime,
                        run_id: str | None = None, model_key: str | None = None,
                        limit: int = 10000):
        principal = actor(request)
        events = await rows(principal, start, end, run_id, model_key, limit)
        output = StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "event_id", "run_id", "model_key", "input_tokens", "output_tokens",
            "total_tokens", "cost_usd", "project_id", "agent_id", "tool_calls",
            "runtime_ms", "storage_bytes", "price_card_version", "source", "occurred_at",
        ])
        for event in events:
            writer.writerow([
                event.event_id, event.run_id, event.model_key, event.input_tokens,
                event.output_tokens, event.total_tokens, str(event.cost_usd),
                event.project_id or "", event.agent_id or "", event.tool_calls,
                event.runtime_ms, event.storage_bytes, event.price_card_version or "",
                event.source, event.occurred_at.isoformat(),
            ])
        return PlainTextResponse(
            output.getvalue(), media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="usage-report.csv"'},
        )

    @router.post("/policies", status_code=201)
    async def configure_policy(request: Request):
        principal = actor(request)
        if quota_policy_service is None or authorize_manage is None:
            raise HTTPException(503, "quota policy administration is not configured")
        allowed = authorize_manage(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "quota management permission required")
        try:
            from agent_platform.quotas.policies import QuotaPolicy
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get("limits"), dict):
                raise ValueError("JSON object with limits is required")
            scope_type = body.get("scope_type")
            scope_id = body.get("scope_id")
            if scope_type == "default":
                if scope_id != "*":
                    raise ValueError("default policy scope_id must be '*'")
                if authorize_default_policy is None:
                    raise PermissionError("platform default policy authorization is not configured")
                default_allowed = authorize_default_policy(principal)
                if inspect.isawaitable(default_allowed):
                    default_allowed = await default_allowed
                if default_allowed is not True:
                    raise PermissionError("platform default policy permission required")
                tenant_id = None
            elif scope_type == "tenant":
                if scope_id != principal.tenant_id:
                    raise PermissionError("tenant policy must match authenticated tenant")
                tenant_id = principal.tenant_id
            elif scope_type in {"project", "subject"}:
                if authorize_policy_scope is None:
                    raise PermissionError("project/subject policy authorization is not configured")
                scope_allowed = authorize_policy_scope(principal, scope_type, scope_id)
                if inspect.isawaitable(scope_allowed):
                    scope_allowed = await scope_allowed
                if scope_allowed is not True:
                    raise PermissionError("quota policy scope is not accessible")
                tenant_id = principal.tenant_id
            else:
                raise ValueError("unsupported quota policy scope")
            policy = QuotaPolicy(
                policy_id=body.get("policy_id", ""), scope_type=scope_type,
                scope_id=scope_id, limits=body["limits"],
                priority=body.get("priority", 100), enabled=body.get("enabled", True),
                version=body.get("version", 1), tenant_id=tenant_id,
                created_by=principal.user_id,
            )
            audit = await quota_policy_service.configure(
                policy, actor_id=principal.user_id, authorize=lambda _: True,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except (KeyError, ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"policy_id": policy.policy_id, "scope_type": policy.scope_type,
                "scope_id": policy.scope_id, "version": policy.version,
                "audit_id": audit.audit_id, "configured": True}

    @router.get("/policies/effective")
    async def effective_policy(request: Request, project_id: str | None = None,
                               subject_id: str | None = None):
        principal = actor(request)
        if quota_policy_service is None:
            raise HTTPException(503, "quota policy resolution is not configured")
        if project_id is not None or subject_id is not None:
            if authorize_policy_scope is None:
                raise HTTPException(403, "project/subject policy authorization is not configured")
            for scope_type, scope_id in (("project", project_id), ("subject", subject_id)):
                if scope_id is None:
                    continue
                allowed = authorize_policy_scope(principal, scope_type, scope_id)
                if inspect.isawaitable(allowed):
                    allowed = await allowed
                if allowed is not True:
                    raise HTTPException(403, "quota policy scope is not accessible")
        from agent_platform.quotas.policies import QuotaPolicyContext
        resolved = await quota_policy_service.resolve(QuotaPolicyContext(
            principal.tenant_id, project_id, subject_id,
        ))
        return {"tenant_id": resolved.tenant_id, "limits": dict(resolved.limits),
                "sources": dict(resolved.sources), "policy_versions": dict(resolved.policy_versions),
                "resolved_at": resolved.resolved_at.isoformat()}

    return router
