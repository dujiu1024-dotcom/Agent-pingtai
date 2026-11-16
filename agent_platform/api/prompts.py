"""Tenant-scoped authenticated Prompt Registry management API."""
from __future__ import annotations

import inspect
import math
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.identity.models import Principal
from agent_platform.observability.prompts import tenant_prompt_key


def _principal(request: Request) -> Principal:
    actor = getattr(request.state, "identity.principal", None)
    if actor is None:
        actor = getattr(getattr(request.state, "identity", None), "principal", None)
    if not isinstance(actor, Principal):
        raise HTTPException(401, "authenticated user required")
    return actor


async def _allowed(
    authorize: Callable[[Principal, str, str], Any] | None,
    actor: Principal, prompt_id: str, action: str,
) -> bool:
    if authorize is None:
        return False
    result = authorize(actor, prompt_id, action)
    if inspect.isawaitable(result):
        result = await result
    return result is True


def _version_view(prompt_id: str, version: Any) -> dict[str, Any]:
    return {
        "prompt_id": prompt_id,
        "version": version.version,
        "variables": list(version.variables),
        "content_hash": version.content_hash,
    }


def create_prompt_registry_router(
    *,
    registry: Any,
    authorize: Callable[[Principal, str, str], Any],
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/prompts", tags=["prompts"])

    async def context(request: Request, prompt_id: str, action: str) -> tuple[Principal, str]:
        actor = _principal(request)
        if not await _allowed(authorize, actor, prompt_id, action):
            raise HTTPException(403, "prompt registry permission required")
        return actor, tenant_prompt_key(actor.tenant_id, prompt_id)

    @router.post("/{prompt_id}/versions", status_code=201)
    async def register_version(prompt_id: str, request: Request):
        actor, storage_key = await context(request, prompt_id, "write")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict) or not isinstance(body.get("template"), str):
            raise HTTPException(400, "template string is required")
        try:
            version = await registry.register(
                storage_key, body["template"], created_by=actor.user_id,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc)) from exc
        return _version_view(prompt_id, version)

    @router.post("/{prompt_id}/activate")
    async def activate_version(prompt_id: str, request: Request):
        actor, storage_key = await context(request, prompt_id, "publish")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        version = body.get("version") if isinstance(body, dict) else None
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(400, "version must be a positive integer")
        try:
            await registry.activate(storage_key, version, updated_by=actor.user_id)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"prompt_id": prompt_id, "active_version": version, "updated_by": actor.user_id}

    @router.post("/{prompt_id}/canary")
    async def configure_canary(prompt_id: str, request: Request):
        actor, storage_key = await context(request, prompt_id, "publish")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        version, percentage = body.get("version"), body.get("percentage")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(400, "version must be a positive integer")
        if isinstance(percentage, bool) or not isinstance(percentage, (int, float)) or not math.isfinite(percentage):
            raise HTTPException(400, "percentage must be a finite number")
        try:
            await registry.set_canary(
                storage_key, version, float(percentage), updated_by=actor.user_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "prompt_id": prompt_id, "canary_version": version,
            "percentage": float(percentage), "updated_by": actor.user_id,
        }

    @router.post("/{prompt_id}/render")
    async def render_prompt(prompt_id: str, request: Request):
        _actor, storage_key = await context(request, prompt_id, "read")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        variables = body.get("variables", {})
        rollout_key = body.get("rollout_key", "")
        version = body.get("version")
        if not isinstance(variables, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in variables.items()
        ):
            raise HTTPException(400, "variables must be a string-to-string object")
        if not isinstance(rollout_key, str):
            raise HTTPException(400, "rollout_key must be a string")
        if version is not None and (
            isinstance(version, bool) or not isinstance(version, int) or version < 1
        ):
            raise HTTPException(400, "version must be a positive integer")
        try:
            rendered, selected = await registry.render(
                storage_key, variables, rollout_key=rollout_key, version=version,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "prompt_id": prompt_id, "version": selected.version,
            "content_hash": selected.content_hash, "rendered": rendered,
        }

    return router
