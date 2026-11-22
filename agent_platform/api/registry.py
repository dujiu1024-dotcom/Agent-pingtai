"""Authenticated registry API; tenant and actor identity come from the host."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.control_plane.registry import (
    RegistryConflict,
    RegistryError,
    RegistryNotFound,
    RegistryService,
)
from agent_platform.identity.models import Principal

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
ServiceResolver = Callable[[Principal], Awaitable[RegistryService]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]


def create_registry_router(
    *, resolve_principal: PrincipalResolver,
    resolve_service: ServiceResolver,
    authorize: Authorizer,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/registry", tags=["registry"])

    async def context(request: Request, kind: str, item_id: str, action: str):
        principal = await resolve_principal(request)
        if not await authorize(principal, kind, item_id, action):
            raise HTTPException(403, "registry resource access denied")
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "registry repository tenant mismatch")
        return principal, service

    def serialize(item: Any) -> dict[str, Any]:
        return {
            "kind": item.kind, "item_id": item.item_id, "version": item.version,
            "definition": dict(item.definition), "status": item.status,
            "created_by": item.created_by, "created_at": item.created_at.isoformat(),
            "published_at": item.published_at.isoformat() if item.published_at else None,
        }

    @router.get("/{kind}")
    async def list_items(kind: str, request: Request, limit: int = 100):
        principal = await resolve_principal(request)
        if not await authorize(principal, kind, "*", "read"):
            raise HTTPException(403, "registry resource access denied")
        service = await resolve_service(principal)
        repository_tenant = getattr(service.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "registry repository tenant mismatch")
        list_items_fn = getattr(service.repository, "list_items", None)
        if list_items_fn is None:
            raise HTTPException(501, "registry repository does not support item listing")
        try:
            items = await list_items_fn(principal.tenant_id, kind, limit=limit)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc

        # 给 tool / skill 加 used_by（哪些 agent 在用）
        agent_usages: dict[str, list[str]] = {}
        if kind in ("tool", "skill"):
            field = "tool_ids" if kind == "tool" else "skill_ids"
            _cf = getattr(service.repository, "_connection_factory", None)
            if _cf is not None:
                conn = await _cf()
                try:
                    cur = await conn.execute(
                        """SELECT a.item_id AS agent_id, v.definition->%s AS refs
                           FROM platform_registry_active a
                           JOIN platform_registry_versions v ON v.tenant_id=a.tenant_id
                             AND v.kind=a.kind AND v.item_id=a.item_id AND v.version=a.active_version
                           WHERE a.tenant_id=%s AND a.kind='agent'""",
                        (field, principal.tenant_id),
                    )
                    for row in await cur.fetchall():
                        agent_id = row["agent_id"]
                        refs = row["refs"] or []
                        if isinstance(refs, list):
                            for ref in refs:
                                agent_usages.setdefault(ref, []).append(agent_id)
                finally:
                    await conn.close()  # type: ignore[union-attr]

        result = []
        for item in items:
            data = serialize(item)
            if kind in ("tool", "skill"):
                data["used_by"] = sorted(set(agent_usages.get(item.item_id, [])))
            result.append(data)
        return {"items": result}

    @router.post("/{kind}/{item_id}/versions", status_code=201)
    async def create_version(kind: str, item_id: str, request: Request):
        principal, service = await context(request, kind, item_id, "write")
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict) or not isinstance(body.get("definition"), dict):
            raise HTTPException(400, "definition object is required")
        try:
            item = await service.create_draft(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id,
                definition=body["definition"], actor_id=principal.user_id,
            )
        except RegistryError as exc:
            raise HTTPException(422, str(exc)) from exc
        return serialize(item)

    @router.post("/{kind}/{item_id}/versions/{version}/publish")
    async def publish(kind: str, item_id: str, version: int, request: Request):
        principal, service = await context(request, kind, item_id, "publish")
        try:
            return serialize(await service.publish(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id, version=version,
            ))
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (RegistryError, RegistryConflict) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/{kind}/{item_id}/rollback/{target_version}")
    async def rollback(kind: str, item_id: str, target_version: int, request: Request):
        principal, service = await context(request, kind, item_id, "publish")
        try:
            return serialize(await service.rollback(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id,
                target_version=target_version,
            ))
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (RegistryError, RegistryConflict) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{kind}/{item_id}/diff/{from_version}/{to_version}")
    async def diff_versions(kind: str, item_id: str, from_version: int, to_version: int, request: Request):
        principal, service = await context(request, kind, item_id, "read")
        if from_version < 1 or to_version < 1 or from_version == to_version:
            raise HTTPException(400, "two distinct positive versions are required")
        left = await service.repository.get(principal.tenant_id, kind, item_id, from_version)
        right = await service.repository.get(principal.tenant_id, kind, item_id, to_version)
        if left is None or right is None:
            raise HTTPException(404, "registry version not found")

        changes = []
        def walk(before, after, path="$"):
            if isinstance(before, dict) and isinstance(after, dict):
                for key in sorted(set(before) | set(after)):
                    child = f"{path}.{key}"
                    if key not in before:
                        changes.append({"path": child, "operation": "add", "after": after[key]})
                    elif key not in after:
                        changes.append({"path": child, "operation": "remove", "before": before[key]})
                    else:
                        walk(before[key], after[key], child)
            elif isinstance(before, list) and isinstance(after, list):
                if before != after:
                    changes.append({"path": path, "operation": "replace", "before": before, "after": after})
            elif before != after:
                changes.append({"path": path, "operation": "replace", "before": before, "after": after})

        walk(dict(left.definition), dict(right.definition))
        return {
            "kind": kind, "item_id": item_id,
            "from_version": from_version, "to_version": to_version,
            "from_status": left.status, "to_status": right.status,
            "changes": changes,
        }

    @router.get("/{kind}/{item_id}/versions/{version}")
    async def get_version(kind: str, item_id: str, version: int, request: Request):
        principal, service = await context(request, kind, item_id, "read")
        item = await service.repository.get(principal.tenant_id, kind, item_id, version)
        if item is None:
            raise HTTPException(404, "registry version not found")
        return serialize(item)

    @router.get("/{kind}/{item_id}/versions")
    async def list_versions(kind: str, item_id: str, request: Request):
        principal, service = await context(request, kind, item_id, "read")
        versions = await service.repository.list_versions(
            principal.tenant_id, kind, item_id,
        )
        return {"items": [serialize(item) for item in versions]}

    @router.post("/{kind}/{item_id}/disable")
    async def disable(kind: str, item_id: str, request: Request):
        principal, service = await context(request, kind, item_id, "publish")
        try:
            return serialize(await service.disable(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id,
                actor_id=principal.user_id,
            ))
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (RegistryError, RegistryConflict) as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{kind}/{item_id}/active")
    async def get_active(kind: str, item_id: str, request: Request):
        principal, service = await context(request, kind, item_id, "read")
        try:
            return serialize(await service.resolve_active(
                tenant_id=principal.tenant_id, kind=kind, item_id=item_id,
            ))
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc

    return router
