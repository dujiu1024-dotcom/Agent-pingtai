"""Authenticated API for multi-variant Model Eval runs."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal


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

    return router
