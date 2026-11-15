"""HTTP API for human approval requests and decisions."""
from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.approval.policy import RiskLevel
from agent_platform.approval.service import ApprovalService
from agent_platform.identity.models import Principal


def create_approval_router(*, service: ApprovalService,
                           authorize_approver: Callable[[Principal], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/approvals", tags=["approvals"])

    def principal(request: Request) -> Principal:
        value = getattr(request.state, "identity.principal", None)
        if not isinstance(value, Principal):
            raise HTTPException(401, "authenticated user required")
        return value

    @router.post("", status_code=201)
    async def request_approval(request: Request):
        actor = principal(request)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            parameters = body.get("parameters", {})
            if not isinstance(parameters, dict):
                raise ValueError("parameters must be an object")
            record = await service.request(
                tenant_id=actor.tenant_id, actor_id=actor.user_id,
                action=str(body.get("action") or ""),
                resource=str(body.get("resource") or ""),
                parameters=parameters,
                risk_level=RiskLevel(int(body.get("risk_level", 0))),
                run_id=str(body["run_id"]) if body.get("run_id") else None,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"approval_id": record.approval_id, "status": record.status,
                "action_hash": record.action_hash, "expires_at": record.expires_at.isoformat()}

    @router.get("")
    async def pending_approvals(request: Request, limit: int = 100):
        actor = principal(request)
        if not await authorize_approver(actor):
            raise HTTPException(403, "approval permission required")
        try:
            records = await service.pending(tenant_id=actor.tenant_id, limit=limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"items": [{
            "approval_id": x.approval_id, "run_id": x.run_id,
            "action": x.action, "resource": x.resource,
            "risk_level": x.risk_level, "requested_by": x.requested_by,
            "status": x.status, "expires_at": x.expires_at.isoformat(),
        } for x in records]}

    @router.post("/{approval_id}/decision")
    async def decide_approval(approval_id: str, request: Request):
        actor = principal(request)
        if not await authorize_approver(actor):
            raise HTTPException(403, "approval permission required")
        try:
            body = await request.json()
            approved = body.get("approved") if isinstance(body, dict) else None
            if not isinstance(approved, bool):
                raise ValueError("approved must be boolean")
            changed = await service.decide(
                tenant_id=actor.tenant_id, approval_id=approval_id,
                approver_id=actor.user_id, approved=approved,
            )
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not changed:
            raise HTTPException(409, "approval is expired, already decided, or unavailable")
        return {"approval_id": approval_id, "status": "approved" if approved else "rejected"}

    return router
