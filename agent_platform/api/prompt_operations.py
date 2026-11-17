"""Prompt test, evaluation-gated release and rollback operations."""
from __future__ import annotations

import inspect
import math
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.observability.prompts import tenant_prompt_key


def create_prompt_operations_router(
    *,
    registry: Any,
    authorize: Callable[[Principal, str, str], Any],
    evaluate_candidate: Callable[[str, str, int], Any],
    audit_writer: Any,
) -> APIRouter:
    """Expose audited prompt operations; release is fail-closed on evaluation/audit."""
    router = APIRouter(prefix="/api/platform/v1/prompts", tags=["prompts"])

    async def context(request: Request, prompt_id: str, action: str) -> tuple[Principal, str]:
        actor = getattr(request.state, "identity.principal", None)
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        allowed = authorize(actor, prompt_id, action)
        if inspect.isawaitable(allowed):
            allowed = await allowed
        if allowed is not True:
            raise HTTPException(403, "prompt operation permission required")
        return actor, tenant_prompt_key(actor.tenant_id, prompt_id)

    async def record(actor: Principal, action: str, prompt_id: str, detail: dict[str, Any]) -> None:
        if audit_writer is None or not callable(getattr(audit_writer, "record", None)):
            raise HTTPException(503, "prompt change audit is not configured")
        try:
            result = audit_writer.record(
                tenant_id=actor.tenant_id, actor_id=actor.user_id,
                action=action, subject_id=prompt_id, outcome="succeeded", detail=detail,
            )
            if inspect.isawaitable(result):
                await result
        except Exception as exc:
            raise HTTPException(503, "prompt audit write failed; operation was not applied") from exc

    async def gate(actor: Principal, prompt_id: str, version: int) -> dict[str, Any]:
        if not callable(evaluate_candidate):
            raise HTTPException(503, "prompt evaluation gate is not configured")
        try:
            evidence = evaluate_candidate(actor.tenant_id, prompt_id, version)
            if inspect.isawaitable(evidence):
                evidence = await evidence
        except Exception as exc:
            raise HTTPException(503, "prompt evaluation gate unavailable") from exc
        passed = getattr(evidence, "passed", None)
        evidence_id = getattr(evidence, "evidence_id", None)
        if not isinstance(evidence, dict):
            passed = evidence.get("passed") if isinstance(evidence, dict) else passed
            evidence_id = evidence.get("evidence_id") if isinstance(evidence, dict) else evidence_id
        if passed is not True or not isinstance(evidence_id, str) or not evidence_id.strip():
            raise HTTPException(409, "prompt candidate did not pass evaluation gate")
        return {"evidence_id": evidence_id, "passed": True}

    @router.post("/{prompt_id}/test")
    async def test_version(prompt_id: str, request: Request):
        actor, key = await context(request, prompt_id, "test")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object is required")
            version = body.get("version")
            variables = body.get("variables", {})
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("version must be a positive integer")
            if not isinstance(variables, dict) or any(
                not isinstance(k, str) or not isinstance(v, str) for k, v in variables.items()
            ):
                raise ValueError("variables must be a string-to-string object")
            rendered, selected = await registry.render(key, variables, version=version)
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        await record(actor, "prompt.version.tested", prompt_id, {
            "version": selected.version, "content_hash": selected.content_hash,
            "variable_names": sorted(variables),
        })
        return {"prompt_id": prompt_id, "version": selected.version,
                "content_hash": selected.content_hash, "rendered": rendered}

    @router.post("/{prompt_id}/release")
    async def release_version(prompt_id: str, request: Request):
        actor, key = await context(request, prompt_id, "publish")
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("JSON object is required")
            version, percentage = body.get("version"), body.get("canary_percentage", 0)
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("version must be a positive integer")
            if isinstance(percentage, bool) or not isinstance(percentage, (int, float)) or not math.isfinite(percentage):
                raise ValueError("canary_percentage must be a finite number")
            if not 0 <= percentage <= 100:
                raise ValueError("canary_percentage must be between 0 and 100")
            # Resolve first so a missing candidate cannot produce an audit or rollout mutation.
            candidate = await registry.resolve(key, version=version)
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        gate_evidence = await gate(actor, prompt_id, version)
        await record(actor, "prompt.version.release_requested", prompt_id, {
            "version": version, "content_hash": candidate.content_hash,
            "canary_percentage": float(percentage),
            "evaluation_evidence_id": gate_evidence["evidence_id"],
        })
        try:
            if percentage == 0:
                await registry.activate(key, version, updated_by=actor.user_id)
            else:
                await registry.set_canary(key, version, float(percentage), updated_by=actor.user_id)
        except (KeyError, ValueError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"prompt_id": prompt_id, "version": version,
                "canary_percentage": float(percentage),
                "evaluation_evidence_id": gate_evidence["evidence_id"]}

    @router.post("/{prompt_id}/rollback")
    async def rollback(prompt_id: str, request: Request):
        actor, key = await context(request, prompt_id, "publish")
        try:
            body = await request.json()
            version = body.get("version") if isinstance(body, dict) else None
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("version must be a positive integer")
            target = await registry.resolve(key, version=version)
        except HTTPException:
            raise
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        await record(actor, "prompt.version.rollback_requested", prompt_id, {
            "target_version": version, "content_hash": target.content_hash,
        })
        await registry.activate(key, version, updated_by=actor.user_id)
        return {"prompt_id": prompt_id, "active_version": version, "rolled_back": True}

    return router
