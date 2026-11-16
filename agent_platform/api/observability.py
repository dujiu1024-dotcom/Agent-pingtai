"""Authenticated observability and tenant-scoped alert operations."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from agent_platform.identity.models import Principal


def _principal(request: Request) -> Principal:
    actor = getattr(getattr(request.state, "identity", None), "principal", None)
    if not isinstance(actor, Principal):
        raise HTTPException(401, "authenticated user required")
    return actor


async def _allowed(authorize: Callable[[Principal], Any] | None, actor: Principal) -> bool:
    if authorize is None:
        return False
    result = authorize(actor)
    if inspect.isawaitable(result):
        result = await result
    return result is True


def create_observability_router(
    *,
    metrics: Any,
    alert_store: Any | None = None,
    authorize_alert_manage: Callable[[Principal], Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/observability", tags=["observability"])

    @router.get("/metrics", response_class=PlainTextResponse)
    async def metrics_endpoint(request: Request):
        _principal(request)
        render = getattr(metrics, "render_prometheus", None)
        if not callable(render):
            raise HTTPException(503, "metrics exporter is not configured")
        return PlainTextResponse(render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @router.get("/alerts")
    async def list_alerts(request: Request, limit: int = Query(default=100, ge=1, le=500)):
        actor = _principal(request)
        if alert_store is None:
            raise HTTPException(503, "alert event store is not configured")
        events = await alert_store.list_open(scope_key=actor.tenant_id, limit=limit)
        return {"items": events, "count": len(events)}

    @router.post("/alerts/{event_id}/acknowledge")
    async def acknowledge_alert(event_id: str, request: Request):
        actor = _principal(request)
        if alert_store is None:
            raise HTTPException(503, "alert event store is not configured")
        if not await _allowed(authorize_alert_manage, actor):
            raise HTTPException(403, "alert management permission required")
        updated = await alert_store.acknowledge(
            event_id, scope_key=actor.tenant_id, actor_id=actor.user_id,
            now=datetime.now(timezone.utc),
        )
        if not updated:
            raise HTTPException(404, "open alert not found")
        return {"event_id": event_id, "status": "acknowledged"}

    @router.post("/alerts/{event_id}/resolve")
    async def resolve_alert(event_id: str, request: Request):
        actor = _principal(request)
        if alert_store is None:
            raise HTTPException(503, "alert event store is not configured")
        if not await _allowed(authorize_alert_manage, actor):
            raise HTTPException(403, "alert management permission required")
        updated = await alert_store.resolve(
            event_id, scope_key=actor.tenant_id, actor_id=actor.user_id,
            now=datetime.now(timezone.utc),
        )
        if not updated:
            raise HTTPException(404, "unresolved alert not found")
        return {"event_id": event_id, "status": "resolved"}

    return router
