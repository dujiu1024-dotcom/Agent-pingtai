"""Authenticated tenant-scoped Run status and cancellation API."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.runtime.lifecycle import RunLifecycleManager
from agent_platform.tools.idempotency import EffectConflict
from agent_platform.streaming.sse import sse_response

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
LifecycleResolver = Callable[[Principal], Awaitable[RunLifecycleManager]]
InterventionResolver = Callable[[Principal], Awaitable[Any]]
DetailResolver = Callable[[Principal], Awaitable[Any]]
ExecutorResolver = Callable[[Principal], Awaitable[Any]]
ToolEffectResolver = Callable[[Principal], Awaitable[Any]]
EventBrokerResolver = Callable[[str], Awaitable[Any]]
BudgetLedgerResolver = Callable[[Principal], Awaitable[Any]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]


def create_runs_router(
    *, resolve_principal: PrincipalResolver,
    resolve_lifecycle: LifecycleResolver,
    authorize: Authorizer,
    resolve_interventions: InterventionResolver | None = None,
    resolve_details: DetailResolver | None = None,
    resolve_executor: ExecutorResolver | None = None,
    resolve_tool_effects: ToolEffectResolver | None = None,
    resolve_event_broker: EventBrokerResolver | None = None,
    resolve_budget_ledger: BudgetLedgerResolver | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/runs", tags=["runs"])

    async def context(request: Request, run_id: str, action: str):
        principal = await resolve_principal(request)
        if not await authorize(principal, "run", run_id, action):
            raise HTTPException(403, "Run access denied")
        lifecycle = await resolve_lifecycle(principal)
        return principal, lifecycle

    def serialize(run: Any) -> dict[str, Any]:
        return {
            "run_id": run.run_id, "status": run.status.value,
            "version": run.version, "checkpoint_ref": run.checkpoint_ref,
            "last_error": run.last_error,
            "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        }

    @router.get("/{run_id}")
    async def get_run(run_id: str, request: Request):
        principal, lifecycle = await context(request, run_id, "read")
        run = await lifecycle.store.get(principal.tenant_id, run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        if resolve_details is not None:
            details = await resolve_details(principal)
            detail = await details.get(tenant_id=principal.tenant_id, run_id=run_id)
            if detail is None:
                raise HTTPException(404, "Run not found")
            result = dict(detail)
        else:
            result = serialize(run)
        if resolve_budget_ledger is not None:
            ledger = await resolve_budget_ledger(principal)
            try:
                snapshot = await ledger.snapshot(principal.tenant_id, run_id)
                result["budget"] = {
                    "max_tokens": snapshot.max_tokens,
                    "max_cost_usd": str(snapshot.max_cost_usd),
                    "reserved_tokens": snapshot.reserved_tokens,
                    "reserved_cost_usd": str(snapshot.reserved_cost_usd),
                    "actual_tokens": snapshot.actual_tokens,
                    "actual_cost_usd": str(snapshot.actual_cost_usd),
                    "remaining_tokens": snapshot.remaining_tokens,
                    "remaining_cost_usd": str(snapshot.remaining_cost_usd),
                    "overrun_reason": snapshot.overrun_reason,
                }
            except KeyError:
                result["budget"] = None
            result["model_call_usage"] = await ledger.list_model_calls(
                tenant_id=principal.tenant_id, run_id=run_id, limit=200,
            )
        return result

    @router.post("/{run_id}/cancel")
    async def cancel_run(run_id: str, request: Request):
        principal, lifecycle = await context(request, run_id, "cancel")
        try:
            run = await lifecycle.cancel(principal.tenant_id, run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return serialize(run)



    if resolve_executor is not None:
        @router.post("/{run_id}/pause")
        async def pause_run(run_id: str, request: Request):
            principal, _ = await context(request, run_id, "pause")
            executor = await resolve_executor(principal)
            try:
                run = await executor.pause(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    reason="paused by authorized human operator",
                )
            except KeyError as exc:
                raise HTTPException(404, "Run not found") from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return serialize(run)

    @router.post("/{run_id}/resume")
    async def resume_run(run_id: str, request: Request):
        principal, lifecycle = await context(request, run_id, "resume")
        service = await resolve_interventions(principal) if resolve_interventions is not None else None
        try:
            ensure_resolved = getattr(service, "ensure_decisions_resolved", None)
            if callable(ensure_resolved):
                await ensure_resolved(tenant_id=principal.tenant_id, run_id=run_id)
            run = await lifecycle.request_resume(principal.tenant_id, run_id)
        except KeyError as exc:
            raise HTTPException(404, "Run not found") from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        if service is not None:
            record_action = getattr(service, "record_action", None)
            if callable(record_action):
                await record_action(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    actor_id=principal.user_id, event_type="human.run_resumed",
                    payload={"status": run.status.value},
                )
        return serialize(run)

    if resolve_interventions is not None:
        @router.post("/{run_id}/interventions", status_code=201)
        async def submit_intervention(run_id: str, request: Request):
            principal, lifecycle = await context(request, run_id, "intervene")
            if await lifecycle.store.get(principal.tenant_id, run_id) is None:
                raise HTTPException(404, "Run not found")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "JSON object is required") from exc
            if not isinstance(body, dict) or not isinstance(body.get("payload"), dict):
                raise HTTPException(400, "kind and payload object are required")
            service = await resolve_interventions(principal)
            try:
                result = await service.submit(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    actor_id=principal.user_id, kind=str(body.get("kind") or ""),
                    payload=body["payload"],
                )
            except KeyError as exc:
                raise HTTPException(404, "Run not found") from exc
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return dict(result)

        @router.post("/{run_id}/interventions/{intervention_id}/decision")
        async def decide_intervention(run_id: str, intervention_id: str, request: Request):
            principal, lifecycle = await context(request, run_id, "intervene")
            if await lifecycle.store.get(principal.tenant_id, run_id) is None:
                raise HTTPException(404, "Run not found")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "JSON object is required") from exc
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("decision"), str)
                or not isinstance(body.get("rationale", ""), str)
            ):
                raise HTTPException(400, "decision and optional rationale are required")
            service = await resolve_interventions(principal)
            try:
                result = await service.decide(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    intervention_id=intervention_id, actor_id=principal.user_id,
                    decision=body["decision"], rationale=body.get("rationale", ""),
                )
            except KeyError as exc:
                raise HTTPException(404, "intervention not found") from exc
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return dict(result)

        @router.post("/{run_id}/result/revisions")
        async def revise_run_result(run_id: str, request: Request):
            principal, lifecycle = await context(request, run_id, "intervene")
            if await lifecycle.store.get(principal.tenant_id, run_id) is None:
                raise HTTPException(404, "Run not found")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "JSON object is required") from exc
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("output"), dict)
                or not isinstance(body.get("reason"), str)
            ):
                raise HTTPException(400, "output object and revision reason are required")
            service = await resolve_interventions(principal)
            try:
                result = await service.revise_result(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    actor_id=principal.user_id, output=body["output"],
                    reason=body["reason"],
                )
            except KeyError as exc:
                raise HTTPException(404, "Run not found") from exc
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except ValueError as exc:
                raise HTTPException(409, str(exc)) from exc
            return dict(result)

        @router.get("/{run_id}/interventions")
        async def list_interventions(run_id: str, request: Request):
            principal, lifecycle = await context(request, run_id, "read")
            if await lifecycle.store.get(principal.tenant_id, run_id) is None:
                raise HTTPException(404, "Run not found")
            service = await resolve_interventions(principal)
            return {"items": await service.list_interventions(
                tenant_id=principal.tenant_id, run_id=run_id,
            )}

        @router.get("/{run_id}/events")
        async def list_run_events(run_id: str, request: Request, limit: int = 200):
            principal, lifecycle = await context(request, run_id, "read")
            if await lifecycle.store.get(principal.tenant_id, run_id) is None:
                raise HTTPException(404, "Run not found")
            service = await resolve_interventions(principal)
            try:
                items = await service.list_events(
                    tenant_id=principal.tenant_id, run_id=run_id, limit=limit,
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            return {"items": items}



    if resolve_event_broker is not None:
        @router.get("/{run_id}/stream")
        async def stream_run_events(run_id: str, request: Request):
            principal, lifecycle = await context(request, run_id, "read")
            if await lifecycle.store.get(principal.tenant_id, run_id) is None:
                raise HTTPException(404, "Run not found")
            broker = await resolve_event_broker(principal.tenant_id)
            try:
                events = await broker.subscribe(
                    tenant_id=principal.tenant_id, run_id=run_id,
                    last_event_id=request.headers.get("last-event-id"),
                )
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from exc
            return sse_response(events)

    if resolve_tool_effects is not None:
        def serialize_effect(effect: Any) -> dict[str, Any]:
            return {
                "run_id": effect.run_id,
                "call_id": effect.call_id,
                "action_digest": effect.action_digest,
                "actor_id": effect.actor_id,
                "state": effect.state,
                "result": effect.result,
                "error": effect.error,
                "created_at": effect.created_at.isoformat() if effect.created_at else None,
                "updated_at": effect.updated_at.isoformat() if effect.updated_at else None,
                "resolution": effect.resolution,
                "reconciled_by": effect.reconciled_by,
                "reconciliation_note": effect.reconciliation_note,
                "reconciled_at": effect.reconciled_at.isoformat() if effect.reconciled_at else None,
            }

        @router.get("/{run_id}/tool-effects/{call_id}")
        async def get_tool_effect(run_id: str, call_id: str, request: Request):
            principal, lifecycle = await context(request, run_id, "read")
            if await lifecycle.store.get(principal.tenant_id, run_id) is None:
                raise HTTPException(404, "Run not found")
            service = await resolve_tool_effects(principal)
            effect = await service.store.get(
                tenant_id=principal.tenant_id, run_id=run_id, call_id=call_id,
            )
            if effect is None:
                raise HTTPException(404, "tool effect not found")
            return serialize_effect(effect)

        @router.post("/{run_id}/tool-effects/{call_id}/reconcile")
        async def reconcile_tool_effect(run_id: str, call_id: str, request: Request):
            principal, lifecycle = await context(request, run_id, "reconcile")
            if await lifecycle.store.get(principal.tenant_id, run_id) is None:
                raise HTTPException(404, "Run not found")
            try:
                body = await request.json()
            except Exception as exc:
                raise HTTPException(400, "JSON object is required") from exc
            if (
                not isinstance(body, dict)
                or not isinstance(body.get("resolution"), str)
                or not isinstance(body.get("note"), str)
            ):
                raise HTTPException(400, "resolution and note are required")
            service = await resolve_tool_effects(principal)
            try:
                effect = await service.reconcile(
                    tenant_id=principal.tenant_id, run_id=run_id, call_id=call_id,
                    operator_id=principal.user_id, resolution=body["resolution"],
                    note=body["note"], result=body.get("result"),
                )
            except PermissionError as exc:
                raise HTTPException(403, str(exc)) from exc
            except KeyError as exc:
                raise HTTPException(404, "tool effect not found") from exc
            except (ValueError, EffectConflict) as exc:
                raise HTTPException(409, str(exc)) from exc
            return serialize_effect(effect)

    return router
