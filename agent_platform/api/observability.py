"""Authenticated observability endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

from agent_platform.identity.models import Principal


def create_observability_router(*, metrics: Any) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/observability", tags=["observability"])

    @router.get("/metrics", response_class=PlainTextResponse)
    async def metrics_endpoint(request: Request):
        actor = getattr(request.state, "identity.principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        render = getattr(metrics, "render_prometheus", None)
        if not callable(render):
            raise HTTPException(503, "metrics exporter is not configured")
        return PlainTextResponse(render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    return router
