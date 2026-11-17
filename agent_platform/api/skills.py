"""Tenant-authorized Skill lifecycle API used by the platform workbench."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.control_plane.registry import RegistryNotFound
from agent_platform.control_plane.skill_registry import SkillPackageError
from agent_platform.identity.models import Principal

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
ServiceResolver = Callable[[Principal], Awaitable[Any]]
Authorizer = Callable[[Principal, str, str], Awaitable[bool]]


def create_skill_router(*, resolve_principal: PrincipalResolver,
                        resolve_service: ServiceResolver,
                        authorize: Authorizer) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/skills", tags=["skills"])

    async def context(request: Request, agent_id: str, action: str):
        principal = await resolve_principal(request)
        if not agent_id or not await authorize(principal, agent_id, action):
            raise HTTPException(403, "Skill Agent access denied")
        service = await resolve_service(principal)
        repository = service.registry.repository
        tenant = getattr(repository, "tenant_id", principal.tenant_id)
        if tenant != principal.tenant_id:
            raise HTTPException(403, "Skill registry tenant mismatch")
        return principal, service

    def installed_dict(item: Any) -> dict[str, Any] | None:
        if item is None:
            return None
        return {
            "skill_id": item.skill_id, "agent_id": item.agent_id,
            "registry_version": item.registry_version,
            "package_version": item.package_version,
        }

    async def status_for(service: Any, tenant_id: str, agent_id: str, skill_id: str):
        installed = await service.agent_store.get(tenant_id, agent_id, skill_id)
        active = await service.registry.repository.get_active(tenant_id, "skill", skill_id)
        if installed is None:
            status = "not_installed"
        elif active is None:
            status = "installed"
        elif installed.registry_version == active.version:
            status = "current"
        else:
            status = "upgrade_available"
        return {"skill_id": skill_id, "status": status,
                "installed": installed_dict(installed),
                "available_version": active.version if active else None}

    @router.get("")
    async def list_skills(request: Request, agent_id: str, limit: int = 100):
        if not 1 <= limit <= 200:
            raise HTTPException(400, "limit must be between 1 and 200")
        principal, service = await context(request, agent_id, "read")
        items = await service.registry.repository.list_items(principal.tenant_id, "skill", limit=limit)
        result = []
        seen = set()
        for candidate in items:
            if candidate.item_id in seen:
                continue
            active = await service.registry.repository.get_active(
                principal.tenant_id, "skill", candidate.item_id,
            )
            if active is None:
                continue
            seen.add(active.item_id)
            manifest = active.definition.get("manifest", {})
            state = await status_for(service, principal.tenant_id, agent_id, active.item_id)
            result.append({
                "skill_id": active.item_id, "name": manifest.get("name", active.item_id),
                "description": manifest.get("description", ""),
                "package_version": manifest.get("version", ""),
                "registry_version": active.version, **state,
            })
        return {"items": result}

    @router.get("/{skill_id}/status")
    async def skill_status(skill_id: str, request: Request, agent_id: str):
        principal, service = await context(request, agent_id, "read")
        return await status_for(service, principal.tenant_id, agent_id, skill_id)

    @router.post("/{skill_id}/install", status_code=201)
    async def install_skill(skill_id: str, request: Request):
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict) or not isinstance(body.get("agent_id"), str):
            raise HTTPException(400, "agent_id is required")
        principal, service = await context(request, body["agent_id"], "install")
        version = body.get("version")
        if type(version) is not int or version < 1:
            raise HTTPException(422, "version must be a positive integer")
        try:
            item = await service.install_for_agent(
                tenant_id=principal.tenant_id, agent_id=body["agent_id"],
                skill_id=skill_id, version=version, actor_id=principal.user_id,
            )
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (SkillPackageError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"installation": installed_dict(item),
                "status": "current" if item else "installed"}

    @router.delete("/{skill_id}/install")
    async def uninstall_skill(skill_id: str, request: Request, agent_id: str):
        principal, service = await context(request, agent_id, "uninstall")
        try:
            await service.uninstall_from_agent(
                tenant_id=principal.tenant_id, agent_id=agent_id,
                skill_id=skill_id, actor_id=principal.user_id,
            )
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        return {"skill_id": skill_id, "agent_id": agent_id, "status": "not_installed"}

    @router.post("/{skill_id}/upgrade")
    async def upgrade_skill(skill_id: str, request: Request):
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict) or not isinstance(body.get("agent_id"), str):
            raise HTTPException(400, "agent_id is required")
        principal, service = await context(request, body["agent_id"], "install")
        version = body.get("version")
        if type(version) is not int or version < 1:
            raise HTTPException(422, "version must be a positive integer")
        try:
            item = await service.install_for_agent(
                tenant_id=principal.tenant_id, agent_id=body["agent_id"],
                skill_id=skill_id, version=version, actor_id=principal.user_id,
            )
        except RegistryNotFound as exc:
            raise HTTPException(404, str(exc)) from exc
        except (SkillPackageError, ValueError) as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"installation": installed_dict(item), "status": "upgraded"}

    return router
