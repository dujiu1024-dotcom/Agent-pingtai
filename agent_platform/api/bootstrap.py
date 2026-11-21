from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter


def create_bootstrap_router(*, auth_enabled: bool, capabilities: Sequence[str] = ()) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1", tags=["bootstrap"])

    @router.get("/bootstrap")
    async def bootstrap() -> dict[str, object]:
        return {
            "auth": {"enabled": bool(auth_enabled)},
            "version": "1.0.0",
            "capabilities": sorted({str(item) for item in capabilities if str(item).strip()}),
        }

    return router
