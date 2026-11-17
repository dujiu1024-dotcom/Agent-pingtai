"""Liveness and dependency-aware readiness endpoints."""
from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse


def create_health_router(*, readiness_checks: Mapping[str, Callable[[], Any]]) -> APIRouter:
    """Build public probes; readiness is unavailable when dependencies are unconfigured."""
    if not isinstance(readiness_checks, Mapping):
        raise TypeError("readiness_checks must be a mapping")
    checks = dict(readiness_checks)
    if any(not isinstance(name, str) or not name.strip() or not callable(check)
           for name, check in checks.items()):
        raise ValueError("readiness checks require non-empty names and callables")
    router = APIRouter(tags=["health"])

    @router.get("/health/live", include_in_schema=False)
    async def live() -> dict[str, str]:
        return {"status": "live"}

    @router.get("/health/ready", include_in_schema=False)
    async def ready():
        if not checks:
            return JSONResponse(
                status_code=503,
                content={"status": "not_ready", "checks": {}, "reason": "readiness checks are not configured"},
            )
        results: dict[str, str] = {}
        for name, check in checks.items():
            try:
                result = check()
                if inspect.isawaitable(result):
                    result = await result
                if result is False:
                    raise RuntimeError("dependency check returned false")
                results[name] = "ok"
            except Exception:
                results[name] = "unavailable"
        healthy = all(value == "ok" for value in results.values())
        return JSONResponse(
            status_code=200 if healthy else 503,
            content={"status": "ready" if healthy else "not_ready", "checks": results},
        )

    return router
