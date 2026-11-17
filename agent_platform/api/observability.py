"""Authenticated observability and tenant-scoped alert operations."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse

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
    evaluation_dataset_repository: Any | None = None,
    evaluation_run_service: Any | None = None,
    authorize_eval_manage: Callable[[Principal], Any] | None = None,
    authorize_eval_review: Callable[[Principal], Any] | None = None,
    authorize_eval_gate_manage: Callable[[Principal], Any] | None = None,
    evaluation_governance_service: Any | None = None,
    operational_health_monitor: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/observability", tags=["observability"])

    @router.get("/metrics", response_class=PlainTextResponse)
    async def metrics_endpoint(request: Request):
        _principal(request)
        render = getattr(metrics, "render_prometheus", None)
        if not callable(render):
            raise HTTPException(503, "metrics exporter is not configured")
        return PlainTextResponse(render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @router.get("/health/capacity")
    async def operational_capacity_health(request: Request):
        _principal(request)
        if operational_health_monitor is None:
            raise HTTPException(503, "operational health monitor is not configured")
        evaluate = getattr(operational_health_monitor, "evaluate", None)
        if not callable(evaluate):
            raise HTTPException(503, "operational health monitor is unavailable")
        try:
            result = evaluate()
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            raise HTTPException(503, "operational health evaluation failed") from None
        if not isinstance(result, dict) or result.get("status") not in {
            "ok", "warning", "critical", "unavailable",
        }:
            raise HTTPException(503, "operational health monitor returned an invalid result")
        status_code = 503 if result["status"] in {"critical", "unavailable"} else 200
        return JSONResponse(status_code=status_code, content=result)

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


    @router.post("/evaluations/datasets/import", status_code=201)
    async def import_evaluation_dataset(request: Request):
        actor = _principal(request)
        if evaluation_dataset_repository is None:
            raise HTTPException(503, "evaluation dataset repository is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation dataset management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            dataset_id = body.get("dataset_id")
            import_format = body.get("format")
            content = body.get("content")
            if dataset_id is None:
                from agent_platform.observability.evaluation_datasets import new_dataset_id
                dataset_id = new_dataset_id()
            if not isinstance(dataset_id, str) or not isinstance(import_format, str) or not isinstance(content, str):
                raise ValueError("dataset_id, format and content must be strings")
            from agent_platform.observability.evaluation_datasets import parse_dataset_import
            cases = parse_dataset_import(content, format=import_format)
            record = await evaluation_dataset_repository.create_version(
                tenant_id=actor.tenant_id, dataset_id=dataset_id, cases=cases,
                created_by=actor.user_id,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "dataset_id": record.dataset_id, "version": record.version,
            "case_count": len(record.cases), "tenant_id": record.tenant_id,
        }

    @router.get("/evaluations/datasets/{dataset_id}/versions")
    async def list_evaluation_dataset_versions(dataset_id: str, request: Request):
        actor = _principal(request)
        if evaluation_dataset_repository is None:
            raise HTTPException(503, "evaluation dataset repository is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation dataset management permission required")
        if not dataset_id.strip():
            raise HTTPException(400, "dataset_id is required")
        rows = await evaluation_dataset_repository.list_versions(
            tenant_id=actor.tenant_id, dataset_id=dataset_id,
        )
        return {"items": [
            {"dataset_id": row.dataset_id, "version": row.version,
             "case_count": len(row.cases), "created_by": row.created_by,
             "created_at": row.created_at}
            for row in rows
        ], "count": len(rows)}

    @router.get("/evaluations/datasets/{dataset_id}/versions/{version}")
    async def get_evaluation_dataset_version(dataset_id: str, version: int, request: Request):
        actor = _principal(request)
        if evaluation_dataset_repository is None:
            raise HTTPException(503, "evaluation dataset repository is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation dataset management permission required")
        try:
            record = await evaluation_dataset_repository.get_version(
                tenant_id=actor.tenant_id, dataset_id=dataset_id, version=version,
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation dataset version not found") from exc
        return {
            "dataset_id": record.dataset_id, "version": record.version,
            "case_count": len(record.cases),
            "cases": [
                {"case_id": case.case_id, "input": case.input,
                 "expected_output": case.expected_output, "rubric": case.rubric,
                 "source_ref": case.source_ref}
                for case in record.cases
            ],
        }

    @router.get("/evaluations/datasets/{dataset_id}/versions/{version}/export",
                response_class=PlainTextResponse)
    async def export_evaluation_dataset_version(
        dataset_id: str, version: int, request: Request,
        format: str = Query(default="jsonl", pattern="^(csv|jsonl)$"),
    ):
        actor = _principal(request)
        if evaluation_dataset_repository is None:
            raise HTTPException(503, "evaluation dataset repository is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation dataset management permission required")
        try:
            record = await evaluation_dataset_repository.get_version(
                tenant_id=actor.tenant_id, dataset_id=dataset_id, version=version,
            )
            from agent_platform.observability.evaluation_datasets import export_dataset
            exported = export_dataset(record, format=format)
        except KeyError as exc:
            raise HTTPException(404, "evaluation dataset version not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        media_type = "text/csv; charset=utf-8" if format == "csv" else "application/x-ndjson"
        return PlainTextResponse(exported, media_type=media_type)

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
        if not await _allowed(authorize_eval_review, actor):
            raise HTTPException(403, "dedicated evaluation reviewer permission required")
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
        if not await _allowed(authorize_eval_review, actor):
            raise HTTPException(403, "dedicated evaluation reviewer permission required")
        rows = await evaluation_repository.list_human_scores(
            tenant_id=actor.tenant_id, eval_run_id=eval_run_id,
        )
        from agent_platform.observability.evaluation_governance import redact_human_score_rows
        safe_rows = redact_human_score_rows(rows)
        return {"items": safe_rows, "count": len(safe_rows)}

    @router.post("/evaluations/{eval_run_id}/compare", status_code=201)
    async def compare_evaluation_runs(eval_run_id: str, request: Request):
        actor = _principal(request)
        if evaluation_governance_service is None:
            raise HTTPException(503, "evaluation governance service is not configured")
        if not await _allowed(authorize_eval_gate_manage, actor):
            raise HTTPException(403, "evaluation gate management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict) or not isinstance(body.get("baseline_run_id"), str):
                raise ValueError("baseline_run_id is required")
            result = await evaluation_governance_service.compare(
                tenant_id=actor.tenant_id, baseline_run_id=body["baseline_run_id"],
                candidate_run_id=eval_run_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation run not found in this tenant") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"comparison_id": result.comparison_id,
                "baseline_run_id": result.baseline_run_id,
                "candidate_run_id": result.candidate_run_id,
                "regressions": list(result.regressions),
                "deltas": [item.__dict__ for item in result.deltas]}

    @router.post("/evaluations/{eval_run_id}/gate", status_code=201)
    async def decide_evaluation_release(eval_run_id: str, request: Request):
        actor = _principal(request)
        if evaluation_governance_service is None:
            raise HTTPException(503, "evaluation governance service is not configured")
        if not await _allowed(authorize_eval_gate_manage, actor):
            raise HTTPException(403, "evaluation gate management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            from agent_platform.observability.evaluation_governance import EvaluationThresholdPolicy
            minimums, maximums = body.get("minimums", {}), body.get("maximums", {})
            refs = body.get("evidence_refs")
            if not isinstance(minimums, dict) or not isinstance(maximums, dict):
                raise ValueError("minimums and maximums must be objects")
            if not isinstance(refs, list) or any(not isinstance(item, str) for item in refs):
                raise ValueError("evidence_refs must be a list of strings")
            policy = EvaluationThresholdPolicy(
                policy_id=body.get("policy_id", ""),
                version=body.get("policy_version", 0),
                minimums=minimums, maximums=maximums,
            )
            evidence = await evaluation_governance_service.decide_release(
                tenant_id=actor.tenant_id, candidate_run_id=eval_run_id,
                baseline_run_id=body.get("baseline_run_id"), policy=policy,
                evidence_refs=tuple(refs),
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation run not found in this tenant") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "evidence_id": evidence.evidence_id, "passed": evidence.passed,
            "failures": list(evidence.failures), "policy_id": evidence.policy_id,
            "policy_version": evidence.policy_version,
            "comparison_id": evidence.comparison_id,
            "content_sha256": evidence.content_sha256,
        }

    return router
