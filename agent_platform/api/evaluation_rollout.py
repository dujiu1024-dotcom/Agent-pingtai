"""Authorized API for explicitly promoting Eval winners into Prompt Lab routing."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal


def _principal(request: Request) -> Principal:
    actor = getattr(request.state, "identity.principal", None)
    if actor is None:
        actor = getattr(getattr(request.state, "identity", None), "principal", None)
    if not isinstance(actor, Principal):
        raise HTTPException(401, "authenticated user required")
    return actor


async def _allowed(authorize: Callable[..., Any] | None, *args: Any) -> bool:
    if authorize is None:
        return False
    result = authorize(*args)
    if inspect.isawaitable(result):
        result = await result
    return result is True


def create_evaluation_rollout_router(
    *,
    bridge: Any,
    authorize_eval_manage: Callable[[Principal], Any],
    authorize_prompt: Callable[[Principal, str, str], Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/api/platform/v1/observability/evaluations/comparisons",
        tags=["evaluation-rollouts"],
    )

    @router.post("/{report_id}/promote")
    async def promote_recommendation(report_id: str, request: Request):
        actor = _principal(request)
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(400, "JSON object is required") from None
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        prompt_id = body.get("prompt_id")
        if not isinstance(prompt_id, str) or not prompt_id.strip():
            raise HTTPException(400, "prompt_id is required")
        if not await _allowed(authorize_eval_manage, actor):
            raise HTTPException(403, "evaluation management permission required")
        if not await _allowed(authorize_prompt, actor, prompt_id, "publish"):
            raise HTTPException(403, "Prompt Lab publish permission required")
        mapping = body.get("model_to_prompt_variant")
        baseline = body.get("baseline_model_key")
        percentage = body.get("candidate_percentage", 10)
        minimum_gain = body.get("minimum_score_gain", 0)
        try:
            result = await bridge.promote(
                tenant_id=actor.tenant_id,
                report_id=report_id,
                prompt_id=prompt_id,
                model_to_prompt_variant=mapping,
                baseline_model_key=baseline,
                candidate_percentage=percentage,
                minimum_score_gain=minimum_gain,
                actor_id=actor.user_id,
            )
        except KeyError:
            raise HTTPException(404, "evaluation comparison report not found") from None
        except PermissionError:
            raise HTTPException(404, "evaluation comparison report not found") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception:
            raise HTTPException(503, "evaluation-to-Prompt rollout could not be completed") from None
        return result

    return router
