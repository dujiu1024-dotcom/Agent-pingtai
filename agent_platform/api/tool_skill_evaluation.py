"""Tenant-scoped Tool/MCP probes and version-pinned Skill dependency reviews."""
from __future__ import annotations

import inspect
from typing import Any, Callable

from fastapi import APIRouter, HTTPException, Request

from agent_platform.control_plane.registry import RegistryService
from agent_platform.control_plane.skill_dependency_review import (
    SkillDependencyReviewError,
    SkillDependencyReviewer,
)
from agent_platform.identity.models import Principal
from agent_platform.tools.mcp_health import MCPHealthChecker


def create_tool_skill_evaluation_router(
    *,
    resolve_principal: Callable[[Request], Any],
    resolve_registry: Callable[[Principal], Any],
    authorize: Callable[[Principal, str, str, str], Any],
    resolve_mcp_checker: Callable[[Principal], Any],
    resolve_skill_reviewer: Callable[[Principal], Any],
    audit_writer: Any,
) -> APIRouter:
    """Expose only host-injected probes/scanners; registry data is never executable."""
    router = APIRouter(prefix="/api/platform/v1", tags=["tool-skill-evaluation"])

    async def resolve(value: Any) -> Any:
        return await value if inspect.isawaitable(value) else value

    async def actor_for(request: Request) -> Principal:
        actor = await resolve(resolve_principal(request))
        if not isinstance(actor, Principal):
            raise HTTPException(401, "authenticated user required")
        if not actor.tenant_id or not actor.user_id:
            raise HTTPException(401, "authenticated tenant and actor are required")
        return actor

    async def audit(
        actor: Principal, action: str, subject_id: str, outcome: str,
        detail: dict[str, Any],
    ) -> None:
        writer = audit_writer
        method = getattr(writer, "record", None)
        if writer is None or not callable(method):
            raise HTTPException(503, "resource audit writer is not configured")
        try:
            result = method(
                tenant_id=actor.tenant_id, actor_id=actor.user_id,
                action=action, subject_id=subject_id, outcome=outcome,
                detail=detail,
            )
            await resolve(result)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "resource audit write failed; operation is not authorized to proceed") from exc

    async def context(
        request: Request, kind: str, item_id: str, version: int, action: str,
    ):
        actor = await actor_for(request)
        allowed = await resolve(authorize(actor, kind, item_id, action))
        if allowed is not True:
            raise HTTPException(403, "registry resource access denied")
        service = await resolve(resolve_registry(actor))
        repository = getattr(service, "repository", None)
        tenant = getattr(repository, "tenant_id", actor.tenant_id)
        if tenant != actor.tenant_id:
            raise HTTPException(403, "registry repository tenant mismatch")
        try:
            item = await repository.get(actor.tenant_id, kind, item_id, version)
        except Exception as exc:
            raise HTTPException(503, "registry lookup unavailable") from exc
        if item is None or item.tenant_id != actor.tenant_id:
            raise HTTPException(404, "registry version not found")
        if item.kind != kind or item.item_id != item_id or item.version != version:
            raise HTTPException(404, "registry version not found")
        if item.status != "published":
            raise HTTPException(409, "only published resource versions can be evaluated")
        return actor, item

    def health_view(record: Any) -> dict[str, Any]:
        return {
            "tenant_id": record.tenant_id,
            "server_id": record.server_id,
            "status": record.status,
            "checked_at": record.checked_at.isoformat(),
            "latency_ms": record.latency_ms,
            "expected_tools": list(record.expected_tools),
            "discovered_tools": list(record.discovered_tools),
            "error_code": record.error_code,
        }

    @router.post("/tools/{item_id}/test-connection")
    async def test_tool_connection(item_id: str, request: Request):
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        version = body.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(400, "a positive published version is required")
        actor, item = await context(request, "tool", item_id, version, "test_connection")
        definition = item.definition
        expected = definition.get("allowlist", definition.get("expected_tools"))
        if not isinstance(expected, list) or not expected or any(
            not isinstance(name, str) or not name.strip() for name in expected
        ):
            raise HTTPException(422, "published Tool definition must declare a non-empty tool allowlist")
        if len(expected) != len(set(expected)):
            raise HTTPException(422, "Tool allowlist must not contain duplicates")
        checker = await resolve(resolve_mcp_checker(actor))
        if not isinstance(checker, MCPHealthChecker):
            raise HTTPException(503, "trusted MCP session factory is not configured")
        subject = f"{item_id}:v{version}"
        await audit(
            actor, "tool.mcp_connection_test", subject, "started",
            {"resource_type": "tool", "resource_id": item_id, "version": version},
        )
        try:
            record = await checker.check(
                tenant_id=actor.tenant_id, server_id=item_id,
                expected_tools=tuple(expected),
            )
        except Exception as exc:
            await audit(
                actor, "tool.mcp_connection_test", subject, "failed",
                {"resource_type": "tool", "resource_id": item_id, "version": version,
                 "error_code": "probe_execution_failed"},
            )
            raise HTTPException(503, "MCP connection test could not be persisted") from exc
        await audit(
            actor, "tool.mcp_connection_test", subject,
            "succeeded" if record.status == "healthy" else "failed",
            {"resource_type": "tool", "resource_id": item_id, "version": version,
             "status": record.status, "error_code": record.error_code},
        )
        return health_view(record)

    @router.post("/skills/{item_id}/evaluate")
    async def evaluate_skill(item_id: str, request: Request):
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(400, "JSON object is required") from exc
        if not isinstance(body, dict):
            raise HTTPException(400, "JSON object is required")
        version = body.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise HTTPException(400, "a positive published version is required")
        actor, item = await context(request, "skill", item_id, version, "evaluate")
        reviewer = await resolve(resolve_skill_reviewer(actor))
        if not isinstance(reviewer, SkillDependencyReviewer):
            raise HTTPException(503, "trusted Skill dependency reviewer is not configured")
        subject = f"{item_id}:v{version}"
        await audit(
            actor, "skill.compatibility_evaluation", subject, "started",
            {"resource_type": "skill", "resource_id": item_id, "version": version},
        )
        try:
            result = await reviewer.review(item=item, actor_id=actor.user_id)
        except SkillDependencyReviewError as exc:
            await audit(
                actor, "skill.compatibility_evaluation", subject, "failed",
                {"resource_type": "skill", "resource_id": item_id, "version": version,
                 "error_code": "review_rejected"},
            )
            raise HTTPException(422, "Skill manifest cannot be evaluated") from exc
        except Exception as exc:
            await audit(
                actor, "skill.compatibility_evaluation", subject, "failed",
                {"resource_type": "skill", "resource_id": item_id, "version": version,
                 "error_code": "review_unavailable"},
            )
            raise HTTPException(503, "Skill compatibility review unavailable") from exc
        await audit(
            actor, "skill.compatibility_evaluation", subject,
            "succeeded" if result.status == "approved" else "failed",
            {"resource_type": "skill", "resource_id": item_id, "version": version,
             "status": result.status, "manifest_digest": result.manifest_digest,
             "finding_count": len(result.findings)},
        )
        return {
            "tenant_id": result.tenant_id,
            "skill_id": result.skill_id,
            "skill_version": result.skill_version,
            "manifest_digest": result.manifest_digest,
            "status": result.status,
            "dependencies": dict(result.dependencies),
            "findings": list(result.findings),
            "reviewed_by": result.reviewed_by,
            "reviewed_at": result.reviewed_at.isoformat(),
        }

    return router
