"""Tenant-scoped digital employee catalog and AI team template API."""
from __future__ import annotations
import inspect
from typing import Any, Callable
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from agent_platform.identity.models import Principal

async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value

async def _authorize(value: Any) -> None:
    result = await _resolve(value)
    if result is False:
        raise HTTPException(403, "permission denied")

class InstallRequest(BaseModel):
    scope: str = Field(default="enterprise", pattern="^(enterprise|project|team)$")
    scope_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=128)
    supervisor_id: str | None = Field(default=None, max_length=128)
    agent_version: str = Field(min_length=1, max_length=128)
    permissions: list[str] = Field(default_factory=list, max_length=100)
    approved_by: str | None = Field(default=None, max_length=128)

class TeamInstallRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    scope: str = Field(default="enterprise", pattern="^(enterprise|project|team)$")
    scope_id: str = Field(min_length=1, max_length=128)
    owner_id: str = Field(min_length=1, max_length=128)
    member_overrides: dict[str, str] = Field(default_factory=dict)
    permissions_by_member: dict[str, list[str]] = Field(default_factory=dict)

def create_digital_employee_router(*, resolve_principal: Callable[[], Any],
    resolve_service: Callable[[Principal], Any], authorize_read: Callable[[Principal], Any],
    authorize_install: Callable[[Principal, str], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/digital-employees", tags=["digital-employees"])
    async def principal() -> Principal:
        value = await _resolve(resolve_principal())
        if not isinstance(value, Principal):
            raise HTTPException(401, "authentication required")
        return value
    async def service_for(p: Principal) -> Any:
        service = await _resolve(resolve_service(p))
        if service is None:
            raise HTTPException(503, "digital employee catalog is not configured")
        return service
    @router.get("")
    async def list_employees(q: str | None = Query(default=None, max_length=160),
        role: str | None = Query(default=None, max_length=80),
        goal: str | None = Query(default=None, max_length=120),
        tags: list[str] = Query(default=[]), page: int = Query(default=1, ge=1),
        page_size: int = Query(default=20, ge=1, le=100), p: Principal = Depends(principal)):
        await _authorize(authorize_read(p))
        return await (await service_for(p)).search_employees(
            tenant_id=p.tenant_id, query=q, role=role, goal=goal, tags=tags,
            page=page, page_size=page_size)
    @router.get("/{employee_id}")
    async def employee_detail(employee_id: str, p: Principal = Depends(principal)):
        await _resolve(authorize_read(p))
        result = await (await service_for(p)).get_employee(
            tenant_id=p.tenant_id, employee_id=employee_id)
        if result is None: raise HTTPException(404, "digital employee not found")
        return result
    @router.post("/{employee_id}/install", status_code=201)
    async def install_employee(employee_id: str, body: InstallRequest,
        p: Principal = Depends(principal)):
        await _authorize(authorize_install(p, body.scope))
        service = await service_for(p)
        employee = await service.get_employee(tenant_id=p.tenant_id, employee_id=employee_id)
        if employee is None or employee.get("status") != "published":
            raise HTTPException(404, "published digital employee not found")
        requested, declared = set(body.permissions), set(employee.get("permission_declarations", []))
        if not requested.issubset(declared):
            raise HTTPException(422, "requested permissions exceed declared capability boundary")
        return await service.install_employee(tenant_id=p.tenant_id, employee_id=employee_id,
            scope=body.scope, scope_id=body.scope_id, owner_id=body.owner_id,
            supervisor_id=body.supervisor_id, agent_version=body.agent_version,
            permissions=sorted(requested), approved_by=body.approved_by, actor_id=p.user_id)
    return router

def create_team_template_router(*, resolve_principal: Callable[[], Any],
    resolve_service: Callable[[Principal], Any], authorize_read: Callable[[Principal], Any],
    authorize_install: Callable[[Principal, str], Any]) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/team-templates", tags=["team-templates"])
    async def principal() -> Principal:
        value = await _resolve(resolve_principal())
        if not isinstance(value, Principal): raise HTTPException(401, "authentication required")
        return value
    async def service_for(p: Principal) -> Any:
        service = await _resolve(resolve_service(p))
        if service is None: raise HTTPException(503, "team template catalog is not configured")
        return service
    @router.get("")
    async def list_templates(q: str | None = Query(default=None, max_length=160),
        goal: str | None = Query(default=None, max_length=120),
        page: int = Query(default=1, ge=1), page_size: int = Query(default=20, ge=1, le=100),
        p: Principal = Depends(principal)):
        await _resolve(authorize_read(p))
        return await (await service_for(p)).search_team_templates(
            tenant_id=p.tenant_id, query=q, goal=goal, page=page, page_size=page_size)
    @router.get("/{template_id}")
    async def template_detail(template_id: str, p: Principal = Depends(principal)):
        await _resolve(authorize_read(p))
        result = await (await service_for(p)).get_team_template(
            tenant_id=p.tenant_id, template_id=template_id)
        if result is None: raise HTTPException(404, "team template not found")
        return result
    @router.post("/{template_id}/install", status_code=201)
    async def install_template(template_id: str, body: TeamInstallRequest,
        p: Principal = Depends(principal)):
        await _resolve(authorize_install(p, body.scope))
        service = await service_for(p)
        template = await service.get_team_template(tenant_id=p.tenant_id, template_id=template_id)
        if template is None or template.get("status") != "published":
            raise HTTPException(404, "published team template not found")
        member_ids = {m["employee_id"] for m in template.get("members", [])}
        if not set(body.member_overrides).issubset(member_ids):
            raise HTTPException(422, "member override references an unknown template member")
        for employee_id, permissions in body.permissions_by_member.items():
            if employee_id not in member_ids:
                raise HTTPException(422, "permission config references an unknown template member")
            employee = await service.get_employee(tenant_id=p.tenant_id, employee_id=employee_id)
            if employee is None or not set(permissions).issubset(
                set(employee.get("permission_declarations", []))):
                raise HTTPException(422, "team member permissions exceed declared capability boundary")
        return await service.install_team_template(tenant_id=p.tenant_id,
            template_id=template_id, name=body.name, scope=body.scope, scope_id=body.scope_id,
            owner_id=body.owner_id, member_overrides=body.member_overrides,
            permissions_by_member=body.permissions_by_member, actor_id=p.user_id)
    return router
