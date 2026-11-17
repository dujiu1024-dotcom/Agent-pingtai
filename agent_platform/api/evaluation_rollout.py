"""Authenticated Model Eval to Prompt Lab rollout bridge API."""
from __future__ import annotations

import inspect
import math
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal


def create_evaluation_rollout_router(
    *, bridge: Any, authorize: Callable[[Principal, str, str], Any], audit_writer: Any,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/evaluations", tags=["evaluation-rollout"])

    @router.post("/{experiment_id}/rollout")
    async def apply_rollout(experiment_id: str, request: Request):
        actor = getattr(request.state, "identity.principal", None)
        if actor is None:
            actor = getattr(getattr(request.state, "identity", None), "principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        if not isinstance(experiment_id, str) or not experiment_id.strip():
            raise HTTPException(400, "experiment_id is required")
        try:
            report = await bridge.experiment_repository.get(actor.tenant_id, experiment_id)
        except Exception as exc:
            raise HTTPException(503, "evaluation report store unavailable") from exc
        if report is None:
            raise HTTPException(404, "experiment report not found")
        if report.tenant_id != actor.tenant_id:
            raise HTTPException(404, "experiment report not found")
        allowed = authorize(actor, report.prompt_id, "publish")
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "prompt publish permission required")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object is required")
            mode = body.get("mode")
            variant = body.get("variant")
            percentage = body.get("rollout_percentage", 10.0)
            gain = body.get("minimum_score_gain", 0.0)
            for value, name in ((percentage, "rollout_percentage"), (gain, "minimum_score_gain")):
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{name} must be a finite number")
            if mode not in {"automatic", "manual"}:
                raise ValueError("mode must be automatic or manual")
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc

        if audit_writer is None or not callable(getattr(audit_writer, "record", None)):
            raise HTTPException(503, "evaluation rollout audit is not configured")
        try:
            audit = audit_writer.record(
                tenant_id=actor.tenant_id, actor_id=actor.user_id,
                action="evaluation.prompt_rollout.requested", subject_id=experiment_id,
                outcome="requested", detail={
                    "prompt_id": report.prompt_id, "mode": mode, "variant": variant,
                    "rollout_percentage": float(percentage),
                    "minimum_score_gain": float(gain),
                },
            )
            if inspect.isawaitable(audit):
                await audit
        except Exception as exc:
            raise HTTPException(503, "audit write failed; rollout was not applied") from exc
        try:
            result = await bridge.apply(
                tenant_id=actor.tenant_id, experiment_id=experiment_id,
                actor=actor.user_id, mode=mode, variant=variant,
                rollout_percentage=percentage, minimum_score_gain=gain,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(403, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(503, "prompt rollout service unavailable") from exc
        return {
            "experiment_id": result.experiment_id, "prompt_id": result.prompt_id,
            "selected_variant": result.selected_variant, "mode": result.mode,
            "weights": dict(result.weights), "policy_revision": result.policy_revision,
        }

    return router
