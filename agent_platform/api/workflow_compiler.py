"""Tenant-authorized Workflow LangGraph compilation and diagnostics API."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.control_plane.registry import RegistryService
from agent_platform.identity.models import Principal
from agent_platform.workflows.definition_compiler import (
    LangGraphWorkflowCompiler,
    WorkflowDefinitionError,
)

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
RegistryResolver = Callable[[Principal], Awaitable[RegistryService]]
CompilerResolver = Callable[[Principal], Awaitable[LangGraphWorkflowCompiler]]
Authorizer = Callable[[Principal, str, str, str], Awaitable[bool]]


def create_workflow_compiler_router(
    *, resolve_principal: PrincipalResolver,
    resolve_registry: RegistryResolver,
    resolve_compiler: CompilerResolver,
    authorize: Authorizer,
) -> APIRouter:
    router = APIRouter(prefix="/api/platform/v1/workflows", tags=["workflow-compiler"])

    async def load(request: Request, workflow_id: str, version: int):
        principal = await resolve_principal(request)
        if not await authorize(principal, "workflow", workflow_id, "compile"):
            raise HTTPException(403, "workflow compile permission denied")
        registry = await resolve_registry(principal)
        repository_tenant = getattr(registry.repository, "tenant_id", principal.tenant_id)
        if repository_tenant != principal.tenant_id:
            raise HTTPException(403, "workflow registry tenant mismatch")
        item = await registry.repository.get(principal.tenant_id, "workflow", workflow_id, version)
        if item is None:
            raise HTTPException(404, "workflow version not found")
        compiler = await resolve_compiler(principal)
        return item, compiler

    @router.get("/{workflow_id}/versions/{version}/diagnostics")
    async def diagnostics(workflow_id: str, version: int, request: Request):
        item, compiler = await load(request, workflow_id, version)
        results = compiler.diagnose(item.definition)
        return {
            "workflow_id": workflow_id,
            "version": version,
            "definition_status": item.status,
            "valid": not results,
            "diagnostics": [entry.to_dict() for entry in results],
        }

    @router.post("/{workflow_id}/versions/{version}/compile")
    async def compile_version(workflow_id: str, version: int, request: Request):
        item, compiler = await load(request, workflow_id, version)
        try:
            graph, artifact = compiler.compile(item.definition)
        except WorkflowDefinitionError as exc:
            raise HTTPException(422, {
                "code": "workflow_compile_rejected",
                "workflow_id": workflow_id,
                "version": version,
                "diagnostics": [entry.to_dict() for entry in exc.diagnostics],
            }) from exc
        # Keep the compiled object process-local for this request only. Runtime
        # admission remains the Run service's responsibility; this API never invokes it.
        del graph
        return {
            "workflow_id": workflow_id,
            "version": version,
            "definition_status": item.status,
            **artifact,
        }

    return router
