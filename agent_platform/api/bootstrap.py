from __future__ import annotations

from collections.abc import Sequence

from fastapi import APIRouter

def resolve_bootstrap_capabilities(
    capabilities: Sequence[str],
    *,
    auth_enabled: bool,
    prompt_registry_configured: bool,
    prompt_evaluation_gate_configured: bool,
) -> tuple[str, ...]:
    """Advertise Prompt Registry only when its authenticated API is configured."""
    values = {
        str(item).strip()
        for item in capabilities
        if str(item).strip() and str(item).strip() not in {
            "prompt_management", "prompt_evaluation_gate",
        }
    }
    if auth_enabled and prompt_registry_configured:
        values.add("prompt_management")
        if prompt_evaluation_gate_configured:
            values.add("prompt_evaluation_gate")
    return tuple(sorted(values))


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
