"""Capability discovery for the independent platform workbench.

The workbench uses this endpoint to distinguish mounted APIs from planned
control-plane capabilities. It deliberately does not advertise placeholder
Agent, task, or approval endpoints as operational.
"""
from __future__ import annotations

from typing import Mapping

from fastapi import APIRouter


def create_workbench_router(*, features: Mapping[str, bool]) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/workbench", tags=["workbench"])
    snapshot = {str(key): bool(value) for key, value in features.items()}

    @router.get("/capabilities")
    async def capabilities():
        return {"features": dict(snapshot)}

    return router
