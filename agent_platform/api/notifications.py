"""Read-only notification feed derived from pending approvals and dead letters."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal


def _principal(request: Request) -> Principal:
    principal = getattr(request.state, "identity.principal", None)
    if principal is None:
        principal = getattr(getattr(request.state, "identity", None), "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(401, "authenticated user required")
    return principal


def create_notifications_router(*, connection_factory: Any) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/notifications", tags=["notifications"])

    @router.get("")
    async def list_notifications(request: Request, limit: int = 50) -> dict[str, Any]:
        principal = _principal(request)
        limit = max(1, min(limit, 200))
        connection = await connection_factory()
        try:
            async with connection:
                cursor = await connection.execute(
                    """SELECT approval_id, run_id, action, resource, risk_level, created_at
                       FROM platform_approvals
                       WHERE tenant_id=%s AND status='pending'
                       ORDER BY created_at DESC LIMIT %s""",
                    (principal.tenant_id, limit),
                )
                approvals = [dict(row) for row in await cursor.fetchall()]
                cursor = await connection.execute(
                    """SELECT run_id, reason, created_at
                       FROM platform_run_dead_letters
                       WHERE tenant_id=%s AND resolved_at IS NULL
                       ORDER BY created_at DESC LIMIT %s""",
                    (principal.tenant_id, limit),
                )
                dead_letters = [dict(row) for row in await cursor.fetchall()]
        finally:
            try:
                await connection.close()
            except Exception:
                pass

        items: list[dict[str, Any]] = []
        for row in approvals:
            items.append({
                "id": str(row["approval_id"]),
                "kind": "approval",
                "level": "warning" if int(row["risk_level"]) >= 3 else "info",
                "title": f"待审批：{row['action']}",
                "detail": f"资源 {row['resource']} · 风险 {row['risk_level']}",
                "at": _iso(row.get("created_at")),
                "path": f"/operations/approvals/{row['approval_id']}",
            })
        for row in dead_letters:
            items.append({
                "id": str(row["run_id"]),
                "kind": "dead_letter",
                "level": "error",
                "title": "Run 进入死信",
                "detail": str(row.get("reason") or ""),
                "at": _iso(row.get("created_at")),
                "path": f"/operations/runs/{row['run_id']}",
            })
        items.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
        return {"items": items[:limit], "count": len(items[:limit])}

    return router


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)
