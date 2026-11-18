"""3D 办公室只读状态接口。

旧版页面（console.html）通过 ``/api/v1/legacy/state`` 读取 SQLite 台账；
这里复用同一份数据结构，把 Agent、任务、运行、用量与订阅映射到新平台
PostgreSQL，让 3D 办公室继续可用且展示真实数据。
"""
from __future__ import annotations

import os
from datetime import datetime
import inspect
from typing import Any, Callable
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal

_OPEN_STATUSES = {"queued", "running", "interrupted"}
# 平台运行状态 → 旧页面使用的 db_status 与中文状态。
_STATUS_MAP = {
    "queued": ("todo", "排队中"),
    "running": ("doing", "执行中"),
    "succeeded": ("done", "已完成"),
    "failed": ("blocked", "失败"),
    "timed_out": ("blocked", "超时"),
    "cancelled": ("canceled", "已取消"),
    "interrupted": ("todo", "已中断"),
}


def _iso(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return None
    return str(value)


def _text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit] + ("…" if len(text) > limit else "")


class OfficeStateService:
    """把平台数据整理成旧 3D 页面所需的 state 结构（只读）。"""

    def __init__(
        self, connection_factory: Callable[[], Any], *, timezone_name: str = "Asia/Shanghai",
    ) -> None:
        self._connect = connection_factory
        self._tz = ZoneInfo(timezone_name)

    async def build(self, tenant_id: str) -> dict[str, Any]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT run_id, task_id, agent_id, template_id, status, input, output, usage, "
                "last_error, created_at, finished_at FROM platform_runs "
                "WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 50",
                (tenant_id,),
            )
            runs = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT status, count(*) AS total FROM platform_runs "
                "WHERE tenant_id=%s GROUP BY status",
                (tenant_id,),
            )
            counts = {row["status"]: int(row["total"]) for row in await cursor.fetchall()}
            cursor = await connection.execute(
                "SELECT COALESCE(SUM(input_tokens + output_tokens), 0) AS tokens "
                "FROM platform_usage_ledger WHERE tenant_id=%s "
                "AND occurred_at >= date_trunc('day', now())",
                (tenant_id,),
            )
            tokens_today = int((await cursor.fetchone() or {}).get("tokens") or 0)
            cursor = await connection.execute(
                "SELECT a.item_id, v.definition FROM platform_registry_active a "
                "JOIN platform_registry_versions v ON v.tenant_id=a.tenant_id "
                "AND v.kind=a.kind AND v.item_id=a.item_id AND v.version=a.active_version "
                "WHERE a.tenant_id=%s AND a.kind='agent' ORDER BY a.item_id",
                (tenant_id,),
            )
            agents = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT v.definition->>'model_id' AS model_id FROM platform_registry_active a "
                "JOIN platform_registry_versions v ON v.tenant_id=a.tenant_id "
                "AND v.kind=a.kind AND v.item_id=a.item_id AND v.version=a.active_version "
                "WHERE a.tenant_id=%s AND a.kind='model_template' LIMIT 1",
                (tenant_id,),
            )
            model_id = (await cursor.fetchone() or {}).get("model_id")
            cursor = await connection.execute(
                "SELECT approval_id, run_id, action, resource, risk_level, created_at "
                "FROM platform_approvals WHERE tenant_id=%s AND status='pending' "
                "ORDER BY created_at DESC LIMIT 10",
                (tenant_id,),
            )
            approvals = await cursor.fetchall()
            cursor = await connection.execute(
                "SELECT count(*) AS total FROM platform_approvals "
                "WHERE tenant_id=%s AND status='pending'",
                (tenant_id,),
            )
            pending_approvals = int((await cursor.fetchone() or {}).get("total") or 0)
            cursor = await connection.execute(
                "SELECT count(*) AS total FROM platform_run_dead_letters "
                "WHERE tenant_id=%s AND resolved_at IS NULL",
                (tenant_id,),
            )
            dead_letters = int((await cursor.fetchone() or {}).get("total") or 0)
            cursor = await connection.execute(
                "SELECT event_type, target, enabled FROM platform_event_subscriptions "
                "WHERE tenant_id=%s ORDER BY created_at",
                (tenant_id,),
            )
            subscriptions = await cursor.fetchall()

        tasks = [self._task(row) for row in runs]
        total = sum(counts.values())
        done = counts.get("succeeded", 0)
        canceled = counts.get("cancelled", 0)
        open_total = sum(counts.get(status, 0) for status in _OPEN_STATUSES)
        closed_total = total - open_total
        today = datetime.now(self._tz).date().isoformat()

        owners: dict[str, dict[str, Any]] = {}
        for task in tasks:
            slot = owners.setdefault(
                task["owner"], {"name": task["owner"], "open": 0, "closed": 0, "hours": 0.0},
            )
            slot["closed" if task["db_status"] in {"done", "canceled"} else "open"] += 1

        events = self._events(runs, approvals)
        running = sorted({str(row["agent_id"]) for row in runs if row["status"] == "running"})

        return {
            "today": today,
            "generated_at": datetime.now(self._tz).isoformat(),
            "project": {"code": "FDE", "gate": {"confirmed": True}},
            "llm": {
                "model": model_id or "未配置",
                "tokens_today": tokens_today,
                "token_limit": int(os.environ.get("AGENCY_DAILY_TOKEN_LIMIT", "200000")),
                "running": running,
            },
            "metrics": {
                "total": total,
                "open": open_total,
                "closed": closed_total,
                "done": done,
                "canceled": canceled,
                "overdue": 0,
                "due_today": 0,
                "hours_total": 0.0,
                "hours_done": 0.0,
                "agents": len(agents),
            },
            "tasks": tasks,
            "milestones": [],
            "owners": sorted(owners.values(), key=lambda item: (-item["open"], item["name"])),
            "agents": [self._agent(row) for row in agents],
            "runs": [self._run(row) for row in runs[:8]],
            "events": {
                "pending": pending_approvals + dead_letters,
                "last": events[0] if events else {},
                "list": events,
            },
            "facts": [],
            "finance": {
                "quotes": {"count": 0, "amount": 0.0, "cost": 0.0},
                "contracts": {"count": 0, "amount": 0.0},
                "invoices": {
                    "issued": 0.0, "received": 0.0, "outstanding": 0.0,
                    "overdue_count": 0, "overdue": 0.0,
                },
                "costs": 0.0,
                "list": [],
            },
            "subscriptions": [
                {
                    "event_type": row["event_type"],
                    "target": row["target"],
                    "action": "通知",
                    "enabled": bool(row["enabled"]),
                    "budget": 0,
                    "spend_today": 0,
                }
                for row in subscriptions
            ],
        }

    @staticmethod
    def _task(row: dict[str, Any]) -> dict[str, Any]:
        status = str(row["status"])
        db_status, state = _STATUS_MAP.get(status, ("todo", status))
        payload = row.get("input") if isinstance(row.get("input"), dict) else {}
        output = row.get("output") if isinstance(row.get("output"), dict) else {}
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        title = payload.get("prompt") or payload.get("title") or row["agent_id"]
        deliverable = output.get("output")
        finished = row.get("finished_at") or row.get("created_at")
        return {
            "id": str(row["run_id"]),
            "title": _text(title, 42),
            "project": "FDE",
            "owner": str(row["agent_id"]),
            "due": finished.date().isoformat() if isinstance(finished, datetime) else "",
            "status": "open" if status in _OPEN_STATUSES else "closed",
            "db_status": db_status,
            "state": state,
            "overdue_days": 0,
            "hours": 0,
            "deliverable": _text(deliverable, 60) if deliverable else "",
            "milestone": "",
            "closed_at": _iso(row.get("finished_at")),
            "last_run": {
                "at": _iso(row.get("finished_at") or row.get("created_at")),
                "role": str(row["agent_id"]),
                "ok": status == "succeeded",
                "path": "",
                "tokens": int(usage.get("total_tokens") or 0),
                "count": 1,
            },
        }

    @staticmethod
    def _agent(row: dict[str, Any]) -> dict[str, Any]:
        definition = row.get("definition") if isinstance(row.get("definition"), dict) else {}
        instructions = definition.get("instructions") or ""
        return {
            "name": row["item_id"],
            "title": definition.get("name") or row["item_id"],
            "desc": _text(instructions, 88),
            "command": f"任务执行：{row['item_id']}",
        }

    @staticmethod
    def _run(row: dict[str, Any]) -> dict[str, Any]:
        usage = row.get("usage") if isinstance(row.get("usage"), dict) else {}
        return {
            "id": str(row["run_id"]),
            "role": str(row["agent_id"]),
            "task_id": str(row.get("task_id") or ""),
            "model": str(row.get("template_id") or ""),
            "output_path": "",
            "tokens_in": int(usage.get("prompt_tokens") or 0),
            "tokens_out": int(usage.get("completion_tokens") or 0),
            "ok": row["status"] == "succeeded",
            "error": row.get("last_error"),
            "created_at": _iso(row.get("created_at")),
        }

    @staticmethod
    def _events(
        runs: list[dict[str, Any]], approvals: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for row in runs:
            status = str(row["status"])
            if status in _OPEN_STATUSES:
                continue
            output = row.get("output") if isinstance(row.get("output"), dict) else {}
            if status == "succeeded":
                event_type, text = "run.succeeded", _text(output.get("output"), 70) or "执行完成"
            elif status == "cancelled":
                event_type, text = "run.cancelled", "任务已取消"
            else:
                event_type, text = "run.error", _text(row.get("last_error"), 70) or "执行失败"
            items.append({
                "at": _iso(row.get("finished_at") or row.get("created_at")),
                "type": event_type,
                "text": text,
                "task": str(row["run_id"]),
                "delivered": False,
            })
        for row in approvals:
            items.append({
                "at": _iso(row.get("created_at")),
                "type": "approval.pending",
                "text": f"{row['action']} → {row['resource']}（风险 {row['risk_level']}）",
                "task": str(row.get("run_id") or ""),
                "delivered": False,
            })
        items.sort(key=lambda item: str(item.get("at") or ""), reverse=True)
        return items[:20]


def create_office_router(*, service: OfficeStateService, resolve_tenant: Callable[[Request], Any] | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/office", tags=["office"])

    @router.get("/state")
    async def state(request: Request):
        principal = getattr(request.state, "identity.principal", None)
        if isinstance(principal, Principal):
            tenant_id = principal.tenant_id
        elif resolve_tenant is not None:
            tenant_id = resolve_tenant(request)
            if inspect.isawaitable(tenant_id):
                tenant_id = await tenant_id
        else:
            raise HTTPException(401, "authenticated user or instance tenant context required")
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise HTTPException(503, "office instance tenant context is unavailable")
        return {"ok": True, "data": await service.build(tenant_id)}

    return router
