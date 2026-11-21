"""Tenant-scoped cross-entity search used by the global search page."""
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


def create_search_router(*, connection_factory: Any) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/search", tags=["search"])

    @router.get("")
    async def search(request: Request, q: str = "", limit: int = 20) -> dict[str, Any]:
        principal = _principal(request)
        query = q.strip()
        if not query:
            return {"query": "", "items": [], "count": 0}
        limit = max(1, min(limit, 50))
        pattern = f"%{query}%"
        items: list[dict[str, Any]] = []

        connection = await connection_factory()
        try:
            async with connection:
                cursor = await connection.execute(
                    """SELECT a.item_id, v.definition
                       FROM platform_registry_active a
                       JOIN platform_registry_versions v
                         ON v.tenant_id=a.tenant_id AND v.kind=a.kind
                        AND v.item_id=a.item_id AND v.version=a.active_version
                       WHERE a.tenant_id=%s AND a.kind='agent'
                         AND (a.item_id ILIKE %s OR v.definition->>'name' ILIKE %s)
                       ORDER BY a.item_id LIMIT %s""",
                    (principal.tenant_id, pattern, pattern, limit),
                )
                for row in await cursor.fetchall():
                    definition = row["definition"] if isinstance(row["definition"], dict) else {}
                    items.append({
                        "type": "agent",
                        "id": row["item_id"],
                        "title": str(definition.get("name") or row["item_id"]),
                        "subtitle": "Agent 定义",
                        "path": f"/admin/agents/{row['item_id']}",
                    })

                cursor = await connection.execute(
                    """SELECT task_id, agent_id, status, input, created_at
                       FROM platform_tasks
                       WHERE tenant_id=%s
                         AND (task_id::text ILIKE %s OR agent_id ILIKE %s
                              OR input->>'prompt' ILIKE %s OR input->>'title' ILIKE %s)
                       ORDER BY created_at DESC LIMIT %s""",
                    (principal.tenant_id, pattern, pattern, pattern, pattern, limit),
                )
                for row in await cursor.fetchall():
                    payload = row["input"] if isinstance(row["input"], dict) else {}
                    items.append({
                        "type": "task",
                        "id": str(row["task_id"]),
                        "title": str(payload.get("prompt") or payload.get("title") or row["task_id"]),
                        "subtitle": f"任务 · {row['agent_id']} · {row['status']}",
                        "path": f"/operations/tasks/{row['task_id']}",
                    })

                cursor = await connection.execute(
                    """SELECT run_id, agent_id, status, created_at
                       FROM platform_runs
                       WHERE tenant_id=%s
                         AND (run_id::text ILIKE %s OR agent_id ILIKE %s)
                       ORDER BY created_at DESC LIMIT %s""",
                    (principal.tenant_id, pattern, pattern, limit),
                )
                for row in await cursor.fetchall():
                    items.append({
                        "type": "run",
                        "id": str(row["run_id"]),
                        "title": f"Run {row['run_id']}",
                        "subtitle": f"运行 · {row['agent_id']} · {row['status']}",
                        "path": f"/operations/runs/{row['run_id']}",
                    })

                cursor = await connection.execute(
                    """SELECT approval_id, action, resource, status, created_at
                       FROM platform_approvals
                       WHERE tenant_id=%s
                         AND (action ILIKE %s OR resource ILIKE %s)
                       ORDER BY created_at DESC LIMIT %s""",
                    (principal.tenant_id, pattern, pattern, limit),
                )
                for row in await cursor.fetchall():
                    items.append({
                        "type": "approval",
                        "id": str(row["approval_id"]),
                        "title": f"审批 {row['action']}",
                        "subtitle": f"资源 {row['resource']} · {row['status']}",
                        "path": f"/operations/approvals/{row['approval_id']}",
                    })
        finally:
            try:
                await connection.close()
            except Exception:
                pass

        grouped: dict[str, list[dict[str, Any]]] = {}
        for item in items:
            grouped.setdefault(item["type"], []).append(item)
        ordered = [item for group in grouped.values() for item in group][:limit]
        return {"query": query, "items": ordered, "count": len(ordered)}

    return router
