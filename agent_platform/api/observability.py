"""Authenticated observability and tenant-scoped alert operations."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from agent_platform.identity.models import Principal


def _principal(request: Request) -> Principal:
    # IdentityASGIMiddleware stores the authenticated principal under the
    # dotted state key; retain compatibility with hosts that expose a nested
    # identity.principal object.
    actor = getattr(request.state, "identity.principal", None)
    if actor is None:
        actor = getattr(getattr(request.state, "identity", None), "principal", None)
    if not isinstance(actor, Principal):
        raise HTTPException(401, "authenticated user required")
    return actor


async def _allowed(authorize: Callable[[Principal], Any] | None, actor: Principal) -> bool:
    if authorize is None:
        return False
    result = authorize(actor)
    if inspect.isawaitable(result):
        result = await result
    return result is True


def create_observability_router(
    *,
    metrics: Any,
    alert_store: Any | None = None,
    authorize_alert_manage: Callable[[Principal], Any] | None = None,
    evaluation_repository: Any | None = None,
    evaluation_run_service: Any | None = None,
    authorize_eval_manage: Callable[[Principal], Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/observability", tags=["observability"])

    @router.get("/metrics", response_class=PlainTextResponse)
    async def metrics_endpoint(request: Request):
        _principal(request)
        render = getattr(metrics, "render_prometheus", None)
        if not callable(render):
            raise HTTPException(503, "metrics exporter is not configured")
        return PlainTextResponse(render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @router.get("/alerts")
    async def list_alerts(request: Request, limit: int = Query(default=100, ge=1, le=500)):
        actor = _principal(request)
        if alert_store is None:
            raise HTTPException(503, "alert event store is not configured")
        events = await alert_store.list_open(scope_key=actor.tenant_id, limit=limit)
        return {"items": events, "count": len(events)}

    @router.post("/alerts/{event_id}/acknowledge")
    async def acknowledge_alert(event_id: str, request: Request):
        actor = _principal(request)
        if alert_store is None:
            raise HTTPException(503, "alert event store is not configured")
        if not await _allowed(authorize_alert_manage, actor):
            raise HTTPException(403, "alert management permission required")
        updated = await alert_store.acknowledge(
            event_id, scope_key=actor.tenant_id, actor_id=actor.user_id,
            now=datetime.now(timezone.utc),
        )
        if not updated:
            raise HTTPException(404, "open alert not found")
        return {"event_id": event_id, "status": "acknowledged"}

    @router.post("/alerts/{event_id}/resolve")
    async def resolve_alert(event_id: str, request: Request):
        actor = _principal(request)
        if alert_store is None:
            raise HTTPException(503, "alert event store is not configured")
        if not await _allowed(authorize_alert_manage, actor):
            raise HTTPException(403, "alert management permission required")
        updated = await alert_store.resolve(
            event_id, scope_key=actor.tenant_id, actor_id=actor.user_id,
            now=datetime.now(timezone.utc),
        )
        if not updated:
            raise HTTPException(404, "unresolved alert not found")
        return {"event_id": event_id, "status": "resolved"}


    @router.post("/evaluations/run", status_code=201)
    async def run_evaluation(request: Request):
        actor = _principal(request)
        if evaluation_run_service is None:
            raise HTTPException(503, "evaluation run service is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation run permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            dataset_id = body.get("dataset_id")
            dataset_version = body.get("dataset_version")
            model_key = body.get("model_key")
            if not isinstance(dataset_id, str) or not isinstance(model_key, str):
                raise ValueError("dataset_id and model_key must be strings")
            result = await evaluation_run_service.run(
                tenant_id=actor.tenant_id, dataset_id=dataset_id,
                dataset_version=dataset_version, model_key=model_key,
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation dataset version not found") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, "evaluation runner failed") from exc
        return {
            "eval_run_id": result.eval_run_id,
            "dataset_id": result.report.dataset_id,
            "dataset_version": result.report.dataset_version,
            "model_key": model_key,
            "gate_passed": result.gate_passed,
            "gate_failures": list(result.gate_failures),
            "pass_rate": result.report.pass_rate,
            "average_score": result.report.average_score,
            "average_recall_at_k": result.report.average_recall_at_k,
        }

    @router.get("/evaluations")
    async def list_evaluation_reports(
        request: Request, dataset_id: str = Query(min_length=1),
        limit: int = Query(default=20, ge=1, le=100),
    ):
        actor = _principal(request)
        if evaluation_repository is None:
            raise HTTPException(503, "evaluation repository is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation review permission required")
        list_reports = getattr(evaluation_repository, "latest_reports", None)
        if not callable(list_reports):
            raise HTTPException(501, "evaluation report history is not supported")
        rows = await list_reports(
            dataset_id, tenant_id=actor.tenant_id, limit=limit,
        )
        return {"items": rows, "count": len(rows)}

    @router.post("/evaluations/{eval_run_id}/scores", status_code=201)
    async def save_human_evaluation_score(eval_run_id: str, request: Request):
        actor = _principal(request)
        if evaluation_repository is None:
            raise HTTPException(503, "evaluation repository is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation review permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            case_id = body.get("case_id")
            score = body.get("score")
            rationale = body.get("rationale", "")
            if not isinstance(case_id, str) or not isinstance(rationale, str):
                raise ValueError("case_id and rationale must be strings")
            score_id = await evaluation_repository.save_human_score(
                tenant_id=actor.tenant_id, eval_run_id=eval_run_id,
                case_id=case_id, reviewer_id=actor.user_id,
                score=score, rationale=rationale,
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation run or case not found") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"score_id": score_id, "eval_run_id": eval_run_id, "case_id": case_id}

    @router.get("/evaluations/{eval_run_id}/scores")
    async def list_human_evaluation_scores(eval_run_id: str, request: Request):
        actor = _principal(request)
        if evaluation_repository is None:
            raise HTTPException(503, "evaluation repository is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation review permission required")
        rows = await evaluation_repository.list_human_scores(
            tenant_id=actor.tenant_id, eval_run_id=eval_run_id,
        )
        return {"items": rows, "count": len(rows)}

    return router
