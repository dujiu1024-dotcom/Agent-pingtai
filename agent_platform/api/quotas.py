"""Tenant-scoped usage and cost reporting API."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, timezone
from io import StringIO
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agent_platform.identity.models import Principal


def create_quota_reporting_router(*, quota_store: Any) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/quotas", tags=["quotas"])

    def actor(request: Request) -> Principal:
        principal = getattr(request.state, "identity.principal", None)
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

    return router
