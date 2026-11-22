"""Authorized, read-only audit query endpoints."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.observability.redaction import redact_payload


def create_audit_router(*, reader: Any, authorize_read: Callable[[Principal], Any], identity_reader: Any | None = None, budget_reader: Any | None = None) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/audit", tags=["audit"])

    @router.get("/tool-events")
    async def list_tool_events(
        request: Request, limit: int = 100,
        actor_id: str | None = None, run_id: str | None = None,
    ):
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_read(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "audit read permission required")
        try:
            items = await reader.list_events(
                tenant_id=principal.tenant_id, limit=limit,
                actor_id=actor_id, run_id=run_id,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tenant_id": principal.tenant_id, "items": [redact_payload(dict(item)) for item in items]}


    @router.get("/identity-events")
    async def list_identity_events(
        request: Request, limit: int = 100,
        actor_id: str | None = None, action: str | None = None,
    ):
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_read(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "audit read permission required")
        if identity_reader is None:
            raise HTTPException(503, "identity audit reader is not configured")
        try:
            items = await identity_reader.list_events(
                tenant_id=principal.tenant_id, limit=limit,
                actor_id=actor_id, action=action,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"tenant_id": principal.tenant_id, "items": [redact_payload(dict(item)) for item in items]}


    @router.get("/run-budget-events")
    async def list_run_budget_events(
        request: Request, run_id: str, limit: int = 200,
    ):
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_read(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "audit read permission required")
        if budget_reader is None:
            raise HTTPException(503, "Run budget audit reader is not configured")
        try:
            items = await budget_reader.list_model_calls(
                tenant_id=principal.tenant_id, run_id=run_id, limit=limit,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "tenant_id": principal.tenant_id, "run_id": run_id,
            "items": [redact_payload(dict(item)) for item in items],
        }

    @router.get("/run-budget-usage")
    async def read_run_budget_usage(request: Request, run_id: str):
        principal = getattr(request.state, "identity.principal", None)
        if principal is None:
            principal = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(principal, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_read(principal)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "audit read permission required")
        if budget_reader is None:
            raise HTTPException(503, "Run budget audit reader is not configured")
        get_report = getattr(budget_reader, "get_run_budget_report", None)
        if get_report is not None:
            report = await get_report(principal.tenant_id, run_id)
            if report is None:
                raise HTTPException(404, "Run budget not found")
            # Ensure tenant_id is surfaced to the caller
            report.setdefault("tenant_id", principal.tenant_id)
            return report
        try:
            snapshot = await budget_reader.snapshot(principal.tenant_id, run_id)
        except KeyError:
            raise HTTPException(404, "Run budget not found")
        calls = await budget_reader.list_model_calls(
            tenant_id=principal.tenant_id, run_id=run_id, limit=200,
        )
        return {
            "tenant_id": principal.tenant_id,
            "run_id": run_id,
            "budget": {
                "max_tokens": snapshot.max_tokens,
                "max_cost_usd": str(snapshot.max_cost_usd),
                "reserved_tokens": snapshot.reserved_tokens,
                "reserved_cost_usd": str(snapshot.reserved_cost_usd),
                "actual_tokens": snapshot.actual_tokens,
                "actual_cost_usd": str(snapshot.actual_cost_usd),
                "remaining_tokens": snapshot.remaining_tokens,
                "remaining_cost_usd": str(snapshot.remaining_cost_usd),
                "overrun_reason": snapshot.overrun_reason,
            },
            "calls": calls,
        }

    return router
