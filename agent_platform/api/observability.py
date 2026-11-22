"""Authenticated observability and tenant-scoped alert operations."""
from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response

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
    evaluation_comparison_service: Any | None = None,
    operational_health_monitor: Any | None = None,
    connection_factory: Any | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/observability", tags=["observability"])

    @router.get("/metrics", response_class=PlainTextResponse)
    async def metrics_endpoint(request: Request):
        _principal(request)
        render = getattr(metrics, "render_prometheus", None)
        if not callable(render):
            raise HTTPException(503, "metrics exporter is not configured")
        return PlainTextResponse(render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @router.get("/metrics/json")
    async def metrics_json_endpoint(request: Request):
        _principal(request)
        export_fn = getattr(metrics, "export_json", None)
        items = export_fn() if callable(export_fn) else []
        if not items:
            # Fallback: derive basic operational metrics from DB counters so the
            # operations dashboard never shows an empty list just because the
            # prometheus registry has not been populated yet.
            try:
                items = await _fallback_db_metrics()
            except Exception:
                items = []
        return {"items": items, "count": len(items)}

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

    @router.get("/evaluations/datasets")
    async def list_evaluation_datasets(
        request: Request, limit: int = Query(default=50, ge=1, le=200),
    ):
        actor = _principal(request)
        if evaluation_repository is None:
            raise HTTPException(503, "evaluation repository is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation dataset management permission required")
        list_fn = getattr(evaluation_repository, "list_datasets", None)
        if not callable(list_fn):
            raise HTTPException(501, "evaluation dataset listing is not supported")
        rows = await list_fn(tenant_id=actor.tenant_id, limit=limit)
        return {"items": rows, "count": len(rows)}

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
        request: Request, dataset_id: str | None = Query(default=None),
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

    @router.post("/evaluations/comparisons", status_code=201)
    async def create_evaluation_comparison(request: Request):
        actor = _principal(request)
        if evaluation_comparison_service is None:
            raise HTTPException(503, "evaluation comparison service is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation comparison management permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict) or set(body) - {
                "dataset_id", "dataset_version", "eval_run_ids",
            }:
                raise ValueError("dataset_id, dataset_version and optional eval_run_ids are required")
            report = await evaluation_comparison_service.create_report(
                tenant_id=actor.tenant_id, dataset_id=body.get("dataset_id"),
                dataset_version=body.get("dataset_version"),
                eval_run_ids=body.get("eval_run_ids"), created_by=actor.user_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation runs not found in this tenant and dataset version") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "evaluation comparison report could not be persisted") from exc
        return report

    @router.get("/evaluations/comparisons/{report_id}")
    async def get_evaluation_comparison(report_id: str, request: Request):
        actor = _principal(request)
        if evaluation_comparison_service is None:
            raise HTTPException(503, "evaluation comparison service is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation comparison read permission required")
        try:
            return await evaluation_comparison_service.get_report(
                tenant_id=actor.tenant_id, report_id=report_id,
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation comparison report not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

    @router.get("/evaluations/comparisons/{report_id}/export")
    async def export_evaluation_comparison(
        report_id: str, request: Request,
        format: str = Query(default="html", pattern="^(html|pdf)$"),
    ):
        actor = _principal(request)
        if evaluation_comparison_service is None:
            raise HTTPException(503, "evaluation comparison service is not configured")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation comparison export permission required")
        try:
            report = await evaluation_comparison_service.get_report(
                tenant_id=actor.tenant_id, report_id=report_id,
            )
            if format == "html":
                return HTMLResponse(
                    evaluation_comparison_service.render_html(report),
                    headers={"Content-Disposition": f'attachment; filename="evaluation-{report_id}.html"'},
                )
            pdf = evaluation_comparison_service.render_pdf(report)
            return Response(
                content=pdf, media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="evaluation-{report_id}.pdf"'},
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation comparison report not found") from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    return router


async def _fallback_db_metrics() -> list[dict[str, Any]]:
    """Derive a handful of gauge metrics from the current DB state.

    Used by ``metrics_json_endpoint`` as a fallback when the Prometheus
    registry has not yet been populated by live exporters.
    """
    import psycopg
    from psycopg.rows import dict_row

    from agent_platform.production_config import load_production_settings

    settings = load_production_settings()
    dsn = settings.postgres_dsn

    # metric_name → 中文显示名（前端直接展示中文名）
    NAME_CN = {
        "platform_runs_total": "运行总数",
        "platform_tool_calls_total": "工具调用数",
        "platform_scheduled_runs_24h": "24h 调度运行数",
        "platform_active_agents_24h": "24h 活跃智能体数",
        "platform_open_alerts": "未关闭告警数",
        "platform_tokens_used_24h": "24h Token 消耗量",
    }
    STATUS_CN = {
        "succeeded": "成功", "failed": "失败", "cancelled": "已取消",
        "interrupted": "中断", "running": "运行中", "pending": "待处理",
        "queued": "排队中", "dead_letter": "死信",
    }
    OUTCOME_CN = {
        "succeeded": "成功", "failed": "失败", "started": "已开始",
        "denied": "已拒绝", "error": "错误",
    }

    def _cn_labels(labels: dict) -> dict:
        out = {}
        for k, v in labels.items():
            if k == "status":
                out["状态"] = STATUS_CN.get(str(v), str(v))
            elif k == "outcome":
                out["结果"] = OUTCOME_CN.get(str(v), str(v))
            elif k == "window":
                out["窗口"] = str(v)
            else:
                out[str(k)] = str(v)
        return out

    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        async with conn.cursor() as cur:
            metrics_out: list[dict[str, Any]] = []

            async def add(name: str, type_: str, help_: str, labels: dict, value: float):
                metrics_out.append({
                    "name": NAME_CN.get(name, name),
                    "type": type_, "help": help_,
                    "labels": _cn_labels(labels), "value": value,
                })

            # 运行总数（按状态）
            await cur.execute(
                "SELECT status, count(*) as cnt FROM platform_runs GROUP BY status"
            )
            for row in await cur.fetchall():
                await add("platform_runs_total", "gauge", "运行总数（按状态）",
                          {"status": row["status"]}, float(row["cnt"]))

            # 工具调用数（按结果）
            await cur.execute(
                "SELECT outcome, count(*) as cnt FROM platform_tool_audit GROUP BY outcome"
            )
            for row in await cur.fetchall():
                await add("platform_tool_calls_total", "gauge", "工具调用数（按结果）",
                          {"outcome": row["outcome"]}, float(row["cnt"]))

            # 最近 24 小时调度任务运行数
            await cur.execute(
                "SELECT count(*) as cnt FROM platform_scheduled_job_runs "
                "WHERE started_at > now() - interval '24 hours'"
            )
            row = await cur.fetchone()
            await add("platform_scheduled_runs_24h", "gauge", "最近 24 小时调度任务运行数",
                      {"window": "24h"}, float(row["cnt"]))

            # 最近 24 小时活跃智能体数
            await cur.execute(
                "SELECT count(DISTINCT agent_id) as cnt FROM platform_runs "
                "WHERE created_at > now() - interval '24 hours'"
            )
            row = await cur.fetchone()
            await add("platform_active_agents_24h", "gauge", "最近 24 小时活跃智能体数",
                      {"window": "24h"}, float(row["cnt"]))

            # 当前未关闭告警数
            await cur.execute("SELECT count(*) as cnt FROM platform_alert_events WHERE status='open'")
            row = await cur.fetchone()
            await add("platform_open_alerts", "gauge", "当前未关闭告警数",
                      {"status": "open"}, float(row["cnt"]))

            # 最近 24 小时 Token 消耗总量
            await cur.execute(
                "SELECT coalesce(sum((usage->>'total_tokens')::int), 0) as total "
                "FROM platform_runs WHERE created_at > now() - interval '24 hours'"
            )
            row = await cur.fetchone()
            await add("platform_tokens_used_24h", "counter", "最近 24 小时 Token 消耗总量",
                      {"window": "24h"}, float(row["total"]))

        return metrics_out
