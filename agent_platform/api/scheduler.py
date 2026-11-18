"""定时作业管理 API：查看作业、手动执行、启停与最近执行记录。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.scheduling.service import (
    EventSubscriptionNotFound,
    ScheduledJobBusy,
    ScheduledJobNotFound,
)


def _principal(request: Request) -> Principal:
    principal = getattr(request.state, "identity.principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(401, "authenticated user required")
    return principal


def create_scheduler_router(*, service: Any) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/scheduler", tags=["scheduler"])

    @router.get("/jobs")
    async def list_jobs(request: Request):
        principal = _principal(request)
        return {
            "items": await service.list_jobs(principal.tenant_id),
            "feishu": service.notifier_status(),
        }

    @router.post("/jobs/{job_id}/run")
    async def run_job(job_id: str, request: Request):
        principal = _principal(request)
        try:
            result = await service.run_job(principal.tenant_id, job_id, trigger="manual")
        except ScheduledJobNotFound as exc:
            raise HTTPException(404, f"定时作业不存在：{job_id}") from exc
        except ScheduledJobBusy as exc:
            raise HTTPException(409, f"定时作业正在执行：{job_id}") from exc
        return result or {"job_id": job_id, "status": "skipped", "message": "作业未到执行时间"}

    @router.patch("/jobs/{job_id}")
    async def update_job(job_id: str, request: Request):
        principal = _principal(request)
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "请求体必须是 JSON 对象") from exc
        if not isinstance(body, dict) or not isinstance(body.get("enabled"), bool):
            raise HTTPException(400, "enabled 必须是布尔值")
        try:
            return await service.set_enabled(principal.tenant_id, job_id, body["enabled"])
        except ScheduledJobNotFound as exc:
            raise HTTPException(404, f"定时作业不存在：{job_id}") from exc

    @router.get("/runs")
    async def list_runs(request: Request, limit: int = 50):
        principal = _principal(request)
        return {"items": await service.list_runs(principal.tenant_id, limit=limit)}

    @router.get("/feishu")
    async def feishu_status(request: Request):
        _principal(request)
        return service.notifier_status()

    # ---------- 事件订阅 ----------
    @router.get("/subscriptions")
    async def list_subscriptions(request: Request):
        principal = _principal(request)
        return {"items": await service.list_subscriptions(principal.tenant_id)}

    @router.post("/subscriptions", status_code=201)
    async def create_subscription(request: Request):
        principal = _principal(request)
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "请求体必须是 JSON 对象") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        event_type = str(body.get("event_type") or "").strip()
        target = str(body.get("target") or "").strip()
        enabled = body.get("enabled", True)
        if not isinstance(enabled, bool):
            raise HTTPException(400, "enabled 必须是布尔值")
        try:
            return await service.create_subscription(
                principal.tenant_id, event_type=event_type, target=target,
                created_by=principal.user_id, enabled=enabled,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.patch("/subscriptions/{subscription_id}")
    async def update_subscription(subscription_id: str, request: Request):
        principal = _principal(request)
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "请求体必须是 JSON 对象") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "请求体必须是 JSON 对象")
        enabled = body.get("enabled")
        if enabled is not None and not isinstance(enabled, bool):
            raise HTTPException(400, "enabled 必须是布尔值")
        target = body.get("target")
        if target is not None and not isinstance(target, str):
            raise HTTPException(400, "target 必须是字符串")
        if enabled is None and target is None:
            raise HTTPException(400, "至少需要提供 enabled 或 target")
        try:
            return await service.update_subscription(
                principal.tenant_id, subscription_id,
                enabled=enabled, target=target,
            )
        except EventSubscriptionNotFound as exc:
            raise HTTPException(404, "事件订阅不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.delete("/subscriptions/{subscription_id}", status_code=204)
    async def delete_subscription(subscription_id: str, request: Request):
        principal = _principal(request)
        try:
            await service.delete_subscription(principal.tenant_id, subscription_id)
        except EventSubscriptionNotFound as exc:
            raise HTTPException(404, "事件订阅不存在") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return None

    return router
