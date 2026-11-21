"""Tenant-scoped enterprise AI value management API."""
from __future__ import annotations

import inspect
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.value_management import AIContribution, HumanBaseline, calculate_value


def create_value_management_router(*, service_resolver: Any, authorize_manage: Any) -> APIRouter:
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
                historical_business_volume=(int(body["historical_business_volume"])
                    if body.get("historical_business_volume") is not None else None),
                volume_period_start=(date.fromisoformat(body["volume_period_start"])
                    if body.get("volume_period_start") is not None else None),
                volume_period_end=(date.fromisoformat(body["volume_period_end"])
                    if body.get("volume_period_end") is not None else None),
                volume_source=(str(body["volume_source"]) if body.get("volume_source") is not None else None),
            )
            saved = await service.save_baseline(baseline)
        except (KeyError, ValueError, TypeError, ArithmeticError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tenant_id": actor.tenant_id, "process_id": saved.process_id,
                "role_id": saved.role_id, "version": saved.version,
                "historical_business_volume": saved.historical_business_volume,
                "volume_period_start": saved.volume_period_start.isoformat() if saved.volume_period_start else None,
                "volume_period_end": saved.volume_period_end.isoformat() if saved.volume_period_end else None,
                "volume_source": saved.volume_source}

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
