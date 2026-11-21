"""Tenant-scoped authenticated Prompt Registry management API."""
from __future__ import annotations

import difflib
import inspect
import math
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Query, Request

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
    evaluate_gate: Callable[..., Any] | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/prompts", tags=["prompts"])

    async def require_evaluation_gate(actor: Principal, prompt_id: str, version: int, action: str) -> dict[str, Any]:
        if evaluate_gate is None:
            raise HTTPException(503, "prompt evaluation gate is not configured")
        try:
            result = evaluate_gate(
                tenant_id=actor.tenant_id, prompt_id=prompt_id,
                version=version, action=action,
            )
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            raise HTTPException(503, "prompt evaluation gate could not be completed") from None
        if (
            not isinstance(result, dict)
            or result.get("gate_passed") is not True
            or not isinstance(result.get("eval_run_id"), str)
            or not result["eval_run_id"].strip()
        ):
            failures = result.get("gate_failures", []) if isinstance(result, dict) else []
            raise HTTPException(409, {
                "message": "prompt candidate did not pass the evaluation gate",
                "gate_failures": failures if isinstance(failures, list) else [],
            })
        return result

    async def context(request: Request, prompt_id: str, action: str) -> tuple[Principal, str]:
        actor = _principal(request)
        if not await _allowed(authorize, actor, prompt_id, action):
            raise HTTPException(403, "prompt registry permission required")
        # Note: PostgresPromptRegistry uses raw prompt_id (no tenant prefix).
        # The tenant_prompt_key helper is retained for future global-registry scenarios
        # but we pass the original prompt_id to match DB records.
        return actor, prompt_id

    @router.get("/")
    async def list_prompts(request: Request, limit: int = Query(default=50, ge=1, le=200)):
        actor = _principal(request)
        list_fn = getattr(registry, "list_prompts", None)
        if not callable(list_fn):
            raise HTTPException(501, "prompt listing is not supported by this registry")
        rows = await list_fn(limit=limit)
        return {"items": rows, "count": len(rows)}

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

    async def invoke(method: Any, *args: Any, **kwargs: Any) -> Any:
        result = method(*args, **kwargs)
        return await result if inspect.isawaitable(result) else result

    def _variant_view(variant: Any) -> dict[str, Any]:
        return {
            "prompt_id": variant.prompt_id,
            "name": variant.name,
            "current_version": variant.current_version,
            "status": variant.status,
            "created_by": variant.created_by,
            "updated_by": variant.updated_by,
        }

    @router.get("/{prompt_id}/versions")
    async def list_versions(prompt_id: str, request: Request):
        _actor, storage_key = await context(request, prompt_id, "read")
        method = getattr(registry, "list_versions", None)
        if not callable(method):
            raise HTTPException(501, "prompt version listing is not supported by this registry")
        try:
            versions = await invoke(method, storage_key)
        except (KeyError, ValueError) as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"items": [_version_view(prompt_id, item) for item in versions],
                "count": len(versions)}

    @router.get("/{prompt_id}/versions/{version}")
    async def get_version(prompt_id: str, version: int, request: Request):
        _actor, storage_key = await context(request, prompt_id, "read")
        if version < 1:
            raise HTTPException(400, "version must be a positive integer")
        try:
            item = await invoke(registry.resolve, storage_key, version=version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {**_version_view(prompt_id, item), "template": item.template}

    @router.get("/{prompt_id}/versions/{version}/diff")
    async def diff_versions(prompt_id: str, version: int, request: Request, base_version: int):
        _actor, storage_key = await context(request, prompt_id, "read")
        if version < 1 or base_version < 1 or version == base_version:
            raise HTTPException(400, "two distinct positive versions are required")
        try:
            before = await invoke(registry.resolve, storage_key, version=base_version)
            after = await invoke(registry.resolve, storage_key, version=version)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        diff = "\n".join(difflib.unified_diff(
            before.template.splitlines(), after.template.splitlines(),
            fromfile=f"v{base_version}", tofile=f"v{version}", lineterm="",
        ))
        return {"prompt_id": prompt_id, "base_version": base_version,
                "version": version, "diff": diff}

    @router.get("/{prompt_id}/variants")
    async def list_variants(prompt_id: str, request: Request):
        _actor, storage_key = await context(request, prompt_id, "read")
        method = getattr(registry, "list_variants", None)
        if not callable(method):
            raise HTTPException(501, "prompt variants are not supported by this registry")
        variants = await invoke(method, storage_key)
        return {"items": [_variant_view(item) for item in variants], "count": len(variants)}

    @router.post("/{prompt_id}/variants", status_code=201)
    async def create_variant(prompt_id: str, request: Request):
        actor, storage_key = await context(request, prompt_id, "write")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        name = body.get("name") if isinstance(body, dict) else None
        version = body.get("version") if isinstance(body, dict) else None
        if not isinstance(name, str) or not name.strip():
            raise HTTPException(400, "variant name is required")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(400, "version must be a positive integer")
        try:
            variant = await invoke(
                registry.create_variant, storage_key, name, version, created_by=actor.user_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _variant_view(variant)

    @router.post("/{prompt_id}/variants/{name}/versions")
    async def assign_variant_version(prompt_id: str, name: str, request: Request):
        actor, storage_key = await context(request, prompt_id, "write")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        version = body.get("version") if isinstance(body, dict) else None
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(400, "version must be a positive integer")
        try:
            variant = await invoke(
                registry.assign_variant, storage_key, name, version, updated_by=actor.user_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _variant_view(variant)

    @router.post("/{prompt_id}/variants/{name}/deprecate")
    async def deprecate_variant(prompt_id: str, name: str, request: Request):
        actor, storage_key = await context(request, prompt_id, "publish")
        try:
            variant = await invoke(
                registry.deprecate_variant, storage_key, name, updated_by=actor.user_id,
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return _variant_view(variant)

    @router.get("/{prompt_id}/variants/{name}/history")
    async def variant_history(prompt_id: str, name: str, request: Request):
        _actor, storage_key = await context(request, prompt_id, "read")
        try:
            items = await invoke(registry.variant_history, storage_key, name)
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"items": list(items), "count": len(items)}

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
        gate = await require_evaluation_gate(actor, prompt_id, version, "activate")
        try:
            await registry.activate(
                storage_key, version, updated_by=actor.user_id,
                evaluation_run_id=gate["eval_run_id"],
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"prompt_id": prompt_id, "active_version": version, "updated_by": actor.user_id,
                "eval_run_id": gate["eval_run_id"]}

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
        gate = await require_evaluation_gate(actor, prompt_id, version, "canary")
        try:
            await registry.set_canary(
                storage_key, version, float(percentage), updated_by=actor.user_id,
                evaluation_run_id=gate["eval_run_id"],
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {
            "prompt_id": prompt_id, "canary_version": version,
            "percentage": float(percentage), "updated_by": actor.user_id,
            "eval_run_id": gate["eval_run_id"],
        }

    @router.post("/{prompt_id}/rollback")
    async def rollback_version(prompt_id: str, request: Request):
        actor, storage_key = await context(request, prompt_id, "publish")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        version = body.get("version") if isinstance(body, dict) else None
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(400, "version must be a positive integer")
        gate = await require_evaluation_gate(actor, prompt_id, version, "rollback")
        try:
            await registry.rollback(
                storage_key, version, updated_by=actor.user_id,
                evaluation_run_id=gate["eval_run_id"],
            )
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"prompt_id": prompt_id, "active_version": version,
                "updated_by": actor.user_id, "eval_run_id": gate["eval_run_id"]}

    @router.get("/{prompt_id}/audit")
    async def prompt_audit(prompt_id: str, request: Request, limit: int = 100):
        _actor, storage_key = await context(request, prompt_id, "read_audit")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise HTTPException(400, "limit must be between 1 and 200")
        list_audit = getattr(registry, "list_audit", None)
        if not callable(list_audit):
            raise HTTPException(501, "prompt audit history is not supported")
        rows = await list_audit(storage_key, limit=limit)
        return {"items": rows, "count": len(rows)}

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
