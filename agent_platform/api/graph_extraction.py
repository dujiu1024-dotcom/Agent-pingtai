"""知识图谱抽取作业 API。

- POST /api/platform/v1/graph/extractions   启动抽取作业
- GET  /api/platform/v1/graph/extractions   最近作业
- GET  /api/platform/v1/graph/extractions/{job_id} 作业详情
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Request

from agent_platform.graph.knowledge_extraction import (
    KnowledgeGraphExtractionService,
)
from agent_platform.identity.models import Principal

PrincipalResolver = Callable[[Request], Awaitable[Principal]]
ReadAuthorizer = Callable[[Principal], Awaitable[Any]]


def create_graph_extraction_router(
    *,
    service: KnowledgeGraphExtractionService,
    resolve_principal: PrincipalResolver,
    authorize_read: ReadAuthorizer,
) -> APIRouter:
    router = APIRouter(
        prefix="/api/platform/v1/graph/extractions",
        tags=["knowledge-graph-extraction"],
    )

    async def require_manage(request: Request) -> tuple[Principal, Any]:
        principal = await resolve_principal(request)
        access = await authorize_read(principal)
        if not bool(getattr(access, "allow_tenant_scope", False)):
            raise HTTPException(
                403, "knowledge.manage permission is required for graph extraction"
            )
        return principal, access

    def serialize(row: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        for key, value in row.items():
            payload[key] = value.isoformat() if hasattr(value, "isoformat") else value
        return payload

    @router.post("", status_code=202)
    async def start_extraction(
        request: Request,
        background: BackgroundTasks,
        payload: dict[str, Any] | None = Body(default=None),
    ) -> dict[str, Any]:
        principal, access = await require_manage(request)
        body = payload if isinstance(payload, dict) else {}

        raw_ids = body.get("document_ids")
        if raw_ids is not None:
            if (
                not isinstance(raw_ids, list)
                or any(not isinstance(value, str) or not value.strip() for value in raw_ids)
            ):
                raise HTTPException(400, "document_ids must be a list of non-empty strings")
            if len(raw_ids) > 50:
                raise HTTPException(400, "document_ids must contain at most 50 items")
            document_ids = [value.strip() for value in raw_ids]
        else:
            document_ids = []

        try:
            limit = int(body.get("limit", 10))
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, "limit must be an integer") from exc
        if limit < 1 or limit > 20:
            raise HTTPException(400, "limit must be between 1 and 20")

        access = await authorize_read(principal)
        documents = await service.list_documents(
            tenant_id=principal.tenant_id,
            document_ids=document_ids,
            limit=limit,
            project_ids=tuple(getattr(access, "project_ids", ()) or ()),
            user_id=getattr(principal, "user_id", None),
        )
        job_id = await service.create_job(
            tenant_id=principal.tenant_id,
            document_ids=[document.document_id for document in documents],
            total=len(documents),
        )
        background.add_task(
            service.run_job,
            tenant_id=principal.tenant_id,
            job_id=job_id,
            documents=documents,
        )
        return {
            "job_id": job_id,
            "status": "queued",
            "total": len(documents),
        }

    @router.get("")
    async def list_extractions(
        request: Request, limit: int = 10,
    ) -> dict[str, Any]:
        principal, _access = await require_manage(request)
        if limit < 1 or limit > 50:
            raise HTTPException(400, "limit must be between 1 and 50")
        rows = await service.list_jobs(tenant_id=principal.tenant_id, limit=limit)
        return {"items": [serialize(row) for row in rows]}

    @router.get("/{job_id}")
    async def get_extraction(request: Request, job_id: str) -> dict[str, Any]:
        principal, _access = await require_manage(request)
        row = await service.get_job(tenant_id=principal.tenant_id, job_id=job_id)
        if row is None:
            raise HTTPException(404, "extraction job not found")
        return serialize(row)

    return router
