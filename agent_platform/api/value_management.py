"""Tenant-scoped enterprise AI value management API."""
from __future__ import annotations

import inspect
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.value_management import AIContribution, HumanBaseline, calculate_value
from agent_platform.value_management_metrics import (
    ToolCostLine, aggregate_value_costs, calculate_business_value_metrics,
)


def create_value_management_router(
    *, service_resolver: Any, authorize_manage: Any, usage_store: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/value-management", tags=["value-management"])

    def principal(request: Request) -> Principal:
        actor = getattr(request.state, "identity.principal", None)
        if actor is None:
            actor = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        return actor

    async def service_for(actor: Principal):
        if service_resolver is None or authorize_manage is None:
            raise HTTPException(503, "AI value management is not configured")
        allowed = authorize_manage(actor)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "AI value management permission required")
        service = service_resolver(actor)
        if inspect.isawaitable(service):
            service = await service
        if service is None:
            raise HTTPException(503, "AI value repository is unavailable")
        return service

    @router.post("/baselines", status_code=201)
    async def create_baseline(request: Request):
        actor = principal(request)
        service = await service_for(actor)
        try:
            body = await request.json()
            baseline = HumanBaseline(
                tenant_id=actor.tenant_id, process_id=str(body["process_id"]),
                role_id=str(body["role_id"]), manual_minutes_per_unit=Decimal(str(body["manual_minutes_per_unit"])),
                labor_cost_per_hour=Decimal(str(body["labor_cost_per_hour"])),
                quality_rate=Decimal(str(body["quality_rate"])), sample_size=int(body["sample_size"]),
                source=str(body["source"]), owner_id=actor.user_id, version=str(body.get("version", "1")),
            )
            saved = await service.save_baseline(baseline)
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tenant_id": actor.tenant_id, "process_id": saved.process_id,
                "role_id": saved.role_id, "version": saved.version}

    @router.post("/attributions", status_code=201)
    async def record_attribution(request: Request):
        actor = principal(request)
        service = await service_for(actor)
        try:
            body = await request.json()
            baseline = await service.get_baseline(
                tenant_id=actor.tenant_id, process_id=str(body["process_id"]),
                role_id=str(body["role_id"]), version=str(body["baseline_version"]),
            )
            if baseline is None:
                raise HTTPException(404, "human baseline not found")
            contribution = AIContribution(
                units_total=int(body["units_total"]), auto_completed=int(body["auto_completed"]),
                assisted_completed=int(body["assisted_completed"]), human_takeovers=int(body["human_takeovers"]),
                rework_units=int(body["rework_units"]), failed_units=int(body["failed_units"]),
                actual_human_minutes=Decimal(str(body["actual_human_minutes"])),
                rework_human_minutes=Decimal(str(body["rework_human_minutes"])),
                agent_run_minutes=Decimal(str(body.get("agent_run_minutes", "0"))),
            )
            result = calculate_value(
                baseline, contribution,
                other_business_benefit=(Decimal(str(body["other_business_benefit"])) if body.get("other_business_benefit") is not None else None),
                ai_cost=(Decimal(str(body["ai_cost"])) if body.get("ai_cost") is not None else None),
                attribution_version=str(body.get("attribution_version", "1")),
                cost_is_settled=body.get("cost_is_settled") is True,
                business_benefit_confirmed=body.get("business_benefit_confirmed") is True,
            )
            record = await service.save_attribution(
                tenant_id=actor.tenant_id, process_id=baseline.process_id, role_id=baseline.role_id,
                baseline_version=baseline.version, actor_id=actor.user_id,
                window_start=datetime.fromisoformat(body["window_start"]),
                window_end=datetime.fromisoformat(body["window_end"]), contribution=contribution, result=result,
                cost_source=body.get("cost_source"), business_evidence=body.get("business_evidence"),
                cost_basis=result.cost_basis,
            )
        except HTTPException:
            raise
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"id": record.id, "saved_minutes": str(result.saved_minutes),
                "saved_hours": str(result.saved_hours), "labor_savings": str(result.labor_savings),
                "ai_cost": str(result.ai_cost) if result.ai_cost is not None else None,
                "net_benefit": str(result.net_benefit) if result.net_benefit is not None else None,
                "roi": str(result.roi) if result.roi is not None else None,
                "financial_status": result.financial_status, "cost_basis": result.cost_basis,
                "attribution_version": result.attribution_version}

    @router.post("/metrics", status_code=201)
    async def record_metrics(request: Request):
        actor = principal(request)
        service = await service_for(actor)
        if usage_store is None or not callable(getattr(usage_store, "list_usage", None)):
            raise HTTPException(503, "tenant usage ledger is not configured")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            run_ids = body.get("run_ids")
            if (
                not isinstance(run_ids, list) or not run_ids
                or len(run_ids) > 100
                or any(not isinstance(item, str) or not item.strip() for item in run_ids)
                or len(set(run_ids)) != len(run_ids)
            ):
                raise ValueError("run_ids must contain 1-100 unique non-empty strings")
            start = datetime.fromisoformat(str(body["window_start"]))
            end = datetime.fromisoformat(str(body["window_end"]))
            if start.tzinfo is None or end.tzinfo is None or end <= start:
                raise ValueError("metrics window must be ordered and timezone-aware")
            metric = calculate_business_value_metrics(
                completed_units=int(body["completed_units"]),
                baseline_cycle_minutes_per_unit=Decimal(str(body["baseline_cycle_minutes_per_unit"])),
                actual_cycle_minutes_per_unit=Decimal(str(body["actual_cycle_minutes_per_unit"])),
                baseline_throughput_per_hour=Decimal(str(body["baseline_throughput_per_hour"])),
                actual_throughput_per_hour=Decimal(str(body["actual_throughput_per_hour"])),
            )
            raw_tool_lines = body.get("tool_cost_lines")
            if raw_tool_lines is not None and not isinstance(raw_tool_lines, list):
                raise ValueError("tool_cost_lines must be a list when supplied")
            tool_lines = None if raw_tool_lines is None else tuple(
                ToolCostLine(
                    tool_key=str(item["tool_key"]), call_count=item["call_count"],
                    cost_usd=Decimal(str(item["cost_usd"])), source=str(item["source"]),
                    pricing_version=str(item["pricing_version"]),
                )
                for item in raw_tool_lines if isinstance(item, dict)
            )
            if raw_tool_lines is not None and len(tool_lines) != len(raw_tool_lines):
                raise ValueError("each tool_cost_line must be a JSON object")
            events = []
            for run_id in run_ids:
                events.extend(await usage_store.list_usage(
                    actor.tenant_id, start, end, run_id=run_id, limit=10000,
                ))
            costs = aggregate_value_costs(
                events, tenant_id=actor.tenant_id, run_ids=run_ids,
                tool_cost_lines=tool_lines,
            )
            process_id = str(body["process_id"])
            role_id = str(body["role_id"])
            attribution_id = str(body["attribution_id"])
            metric_version = str(body.get("metric_version", "1"))
            source_metadata = body.get("source_metadata") or {}
            if not isinstance(source_metadata, dict):
                raise ValueError("source_metadata must be a JSON object")
            try:
                snapshot_id = await service.save_metrics(
                    tenant_id=actor.tenant_id, attribution_id=attribution_id,
                    process_id=process_id, role_id=role_id, window_start=start,
                    window_end=end, metrics=metric, run_ids=run_ids,
                    cost_breakdown=costs, metric_version=metric_version,
                    source_metadata=source_metadata, actor_id=actor.user_id,
                )
            except KeyError as exc:
                raise HTTPException(404, "attribution not found for authenticated tenant") from exc
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(400, f"missing required field: {exc.args[0]}") from exc
        except (ValueError, TypeError, ArithmeticError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "id": snapshot_id, "attribution_id": attribution_id,
            "metrics": {
                "completed_units": metric.completed_units,
                "baseline_cycle_minutes_per_unit": str(metric.baseline_cycle_minutes_per_unit),
                "actual_cycle_minutes_per_unit": str(metric.actual_cycle_minutes_per_unit),
                "cycle_time_delta_minutes_per_unit": str(metric.cycle_time_delta_minutes_per_unit),
                "cycle_time_reduction_rate": str(metric.cycle_time_reduction_rate),
                "cycle_minutes_saved": str(metric.cycle_minutes_saved),
                "baseline_throughput_per_hour": str(metric.baseline_throughput_per_hour),
                "actual_throughput_per_hour": str(metric.actual_throughput_per_hour),
                "throughput_delta_per_hour": str(metric.throughput_delta_per_hour),
                "throughput_change_rate": str(metric.throughput_change_rate),
                "metric_version": metric_version,
            },
            "costs": costs,
            "window_start": start.isoformat(), "window_end": end.isoformat(),
        }

    @router.get("/summary")
    async def summary(request: Request, start: datetime, end: datetime):
        actor = principal(request)
        service = await service_for(actor)
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise HTTPException(400, "start and end must be ordered timezone-aware timestamps")
        data = await service.summary(tenant_id=actor.tenant_id, start=start, end=end)
        return {"tenant_id": actor.tenant_id, "period_start": start.isoformat(),
                "period_end": end.isoformat(), "items": data,
                "note": "Unknown financial values remain null; agent runtime is not counted as labor savings."}

    return router
