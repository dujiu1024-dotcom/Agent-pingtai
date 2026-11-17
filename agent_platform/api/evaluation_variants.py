"""Authenticated API for multi-variant Model Eval runs."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response

from agent_platform.identity.models import Principal
from agent_platform.observability.evaluation_reports import (
    build_variant_comparison, render_variant_report_html, render_variant_report_pdf,
)


def create_eval_variants_router(
    *, service: Any, authorize_manage: Callable[[Principal], Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/api/platform/v1/observability/evaluations",
        tags=["model-evaluation"],
    )

    @router.post("/variants/run", status_code=201)
    async def run_variants(request: Request):
        actor = getattr(request.state, "identity.principal", None)
        if actor is None:
            actor = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_manage(actor)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "evaluation run permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object required")
            result = await service.run(
                tenant_id=actor.tenant_id,
                dataset_id=body.get("dataset_id"),
                dataset_version=body.get("dataset_version"),
                prompt=body.get("prompt"),
                model_variants=body.get("model_variants"),
            )
        except KeyError as exc:
            raise HTTPException(404, "evaluation dataset version not found") from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(502, "evaluation variant run failed") from exc
        return {
            "eval_run_id": result.eval_run_id,
            "dataset_id": result.dataset_id,
            "dataset_version": result.dataset_version,
            "variants": [{
                "model_variant": row.model_variant,
                "case_count": row.case_count,
                "passed_count": row.passed_count,
                "pass_rate": row.pass_rate,
                "average_score": row.average_score,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "total_tokens": row.total_tokens,
                "cost_usd": row.cost_usd,
                "average_latency_ms": row.average_latency_ms,
                "p50_latency_ms": row.p50_latency_ms,
                "p95_latency_ms": row.p95_latency_ms,
                "cases": [{
                    "case_id": case.case_id, "score": case.score,
                    "passed": case.passed, "scoring_method": case.scoring_method,
                } for case in row.cases],
            } for row in result.variant_results],
        }

    async def load_report_run(request: Request, run_id: str):
        actor = getattr(request.state, "identity.principal", None)
        if actor is None:
            actor = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize_manage(actor)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "evaluation report permission required")
        repository = getattr(service, "run_repository", None)
        if repository is None or not callable(getattr(repository, "get_run", None)):
            raise HTTPException(503, "evaluation run repository is not configured")
        try:
            run = await repository.get_run(tenant_id=actor.tenant_id, run_id=run_id)
            return build_variant_comparison(run)
        except KeyError as exc:
            raise HTTPException(404, "evaluation run not found") from exc
        except ValueError as exc:
            raise HTTPException(422, "evaluation run report data is invalid") from exc
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "evaluation report is unavailable") from exc

    @router.get("/variants/{run_id}/comparison")
    async def compare_variants(run_id: str, request: Request):
        return await load_report_run(request, run_id)

    @router.get("/variants/{run_id}/report")
    async def export_variant_report(
        run_id: str, request: Request,
        report_format: str = Query(default="html", alias="format"),
    ):
        comparison = await load_report_run(request, run_id)
        if report_format == "html":
            content = render_variant_report_html(comparison)
            return Response(
                content=content, media_type="text/html",
                headers={"Content-Disposition": f'attachment; filename="eval-{run_id}.html"'},
            )
        if report_format == "pdf":
            content = render_variant_report_pdf(comparison)
            return Response(
                content=content, media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="eval-{run_id}.pdf"'},
            )
        raise HTTPException(400, "format must be html or pdf")

    return router
