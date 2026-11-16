"""FastAPI router for Knowledge and Memory use cases.

Authentication is supplied by the host application; this router never trusts
tenant/user identifiers from request bodies.
"""
from __future__ import annotations
import hashlib
from datetime import datetime
from typing import Any, Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from agent_platform.api.identity_context import IdentityContextResolver
from agent_platform.knowledge.contracts import Document, KnowledgePrincipal, KnowledgeScope
from agent_platform.knowledge.rag import RAGService
from agent_platform.knowledge.service import KnowledgeService
from agent_platform.memory.extraction import MemoryExtractionService, MemoryTurn
from agent_platform.memory.service import MemoryCandidate, MemoryPrincipal, MemoryScope, MemoryService
KnowledgePrincipalResolver=Callable[[Request],Awaitable[KnowledgePrincipal]]
MemoryPrincipalResolver=Callable[[Request],Awaitable[MemoryPrincipal]]
async def _authorize_document_update(
    knowledge: KnowledgeService,
    principal: KnowledgePrincipal,
    *,
    document_id: str,
    scope: KnowledgeScope,
    project_id: str | None,
) -> None:
    existing = await knowledge.repository.get_document(
        principal.tenant_id, document_id,
    )
    if existing is None:
        return
    if existing.scope is not scope or existing.project_id != project_id:
        raise HTTPException(409, "document access scope cannot be changed in place")
    if existing.scope is KnowledgeScope.PRIVATE:
        allowed = (
            existing.owner_user_id == principal.user_id
            or "knowledge.manage" in principal.permissions
        )
    elif existing.scope is KnowledgeScope.PROJECT:
        allowed = (
            "knowledge.manage" in principal.permissions
            or existing.project_id in principal.manage_project_ids
        )
    else:
        allowed = "knowledge.manage" in principal.permissions
    if not allowed:
        raise HTTPException(403, "document update permission denied")


def _memory_visible_for_review(record: Any,principal: MemoryPrincipal)->bool:
    candidate=record.candidate
    if candidate.tenant_id!=principal.tenant_id:return False
    if candidate.scope is MemoryScope.USER:return candidate.scope_id==principal.user_id or "memory.manage" in principal.permissions
    if candidate.scope is MemoryScope.PROJECT:
        return candidate.scope_id in principal.manage_project_ids or "memory.manage" in principal.permissions
    if candidate.scope is MemoryScope.AGENT:
        return candidate.scope_id in principal.manage_agent_ids or "memory.manage" in principal.permissions
    return "memory.manage" in principal.permissions
def create_knowledge_memory_router(
    *,
    knowledge: KnowledgeService,
    memory: MemoryService,
    resolve_knowledge_principal: KnowledgePrincipalResolver | None = None,
    resolve_memory_principal: MemoryPrincipalResolver | None = None,
    rag: RAGService | None = None,
    memory_extraction: MemoryExtractionService | None = None,
    identity_repository: Any | None = None,
) -> APIRouter:
    if identity_repository is not None:
        resolver = IdentityContextResolver(identity_repository)
        resolve_knowledge_principal = resolve_knowledge_principal or resolver.knowledge
        resolve_memory_principal = resolve_memory_principal or resolver.memory
    if resolve_knowledge_principal is None or resolve_memory_principal is None:
        raise ValueError(
            "provide identity_repository or both explicit principal resolvers"
        )
    router=APIRouter(prefix="/api/platform/v1",tags=["knowledge","memory"])
    @router.get("/knowledge/documents")
    async def list_documents(request: Request,limit: int=100):
        principal=await resolve_knowledge_principal(request)
        try:documents=await knowledge.list_documents(principal=principal,limit=limit)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        return {"items":[{"document_id":d.document_id,"version":d.version,"title":d.title,"source_uri":d.source_uri,"scope":d.scope.value,"project_id":d.project_id,"media_type":d.media_type,"content_hash":d.content_hash} for d in documents]}
    @router.post("/knowledge/documents")
    async def ingest_document(request: Request):
        principal=await resolve_knowledge_principal(request);body=await request.json()
        try:scope=KnowledgeScope(body.get("scope","private"))
        except ValueError as exc:raise HTTPException(400,"invalid document scope") from exc
        project_id=body.get("project_id")
        # 已存在文档的身份与作用域不可原地变更：先判不可变（409），再判新作用域权限（403）。
        try:document_id=str(UUID(str(body["document_id"]))) if body.get("document_id") else str(uuid4())
        except ValueError as exc:raise HTTPException(400,"document_id must be a UUID") from exc
        await _authorize_document_update(
            knowledge, principal, document_id=document_id,
            scope=scope, project_id=project_id,
        )
        if scope is KnowledgeScope.TENANT and "knowledge.manage" not in principal.permissions:raise HTTPException(403,"knowledge.manage role required")
        if scope is KnowledgeScope.PROJECT and project_id not in principal.manage_project_ids:raise HTTPException(403,"project knowledge.manage permission required")
        source_uri=body.get("source_uri") or ("upload://"+document_id)
        content=body.get("content")
        if not isinstance(content,str) or not content.strip():raise HTTPException(400,"text content is required")
        raw=content.encode("utf-8")
        document=Document(tenant_id=principal.tenant_id,document_id=document_id,title=str(body.get("title") or "Untitled"),source_uri=source_uri,content_hash=hashlib.sha256(raw).hexdigest(),scope=scope,project_id=project_id,owner_user_id=principal.user_id if scope is KnowledgeScope.PRIVATE else None,media_type=str(body.get("media_type") or "text/plain"))
        try:result=await knowledge.ingest(document=document,content=raw)
        except (ValueError,RuntimeError) as exc:raise HTTPException(400,str(exc)) from exc
        return {"document_id":result.document.document_id,"version":result.document.version,"chunk_count":len(result.chunks),"skipped_unchanged":result.skipped_unchanged}
    @router.post("/knowledge/upload")
    async def upload_document(request: Request,title: str,scope: str="private",project_id: str|None=None,source_uri: str|None=None):
        principal=await resolve_knowledge_principal(request)
        try:document_scope=KnowledgeScope(scope)
        except ValueError as exc:raise HTTPException(400,"invalid document scope") from exc
        if document_scope is KnowledgeScope.TENANT and "knowledge.manage" not in principal.permissions:
            raise HTTPException(403,"knowledge.manage role required")
        if document_scope is KnowledgeScope.PROJECT and project_id not in principal.manage_project_ids:
            raise HTTPException(403,"project knowledge.manage permission required")
        raw=await request.body()
        if not raw:raise HTTPException(400,"uploaded document is empty")
        media_type=request.headers.get("content-type","application/octet-stream").split(";")[0].strip()
        document_id=str(uuid4())
        document=Document(tenant_id=principal.tenant_id,document_id=document_id,title=title,source_uri=source_uri or ("upload://"+document_id),content_hash=hashlib.sha256(raw).hexdigest(),scope=document_scope,project_id=project_id,owner_user_id=principal.user_id if document_scope is KnowledgeScope.PRIVATE else None,media_type=media_type)
        try:result=await knowledge.ingest(document=document,content=raw)
        except (ValueError,RuntimeError) as exc:raise HTTPException(400,str(exc)) from exc
        return {"document_id":result.document.document_id,"version":result.document.version,"chunk_count":len(result.chunks),"media_type":media_type}
    @router.delete("/knowledge/documents/{document_id}",status_code=204)
    async def delete_document(document_id: str,request: Request):
        principal=await resolve_knowledge_principal(request)
        try:deleted=await knowledge.delete_document(principal=principal,document_id=document_id)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        if not deleted:raise HTTPException(404,"document not found")
        return JSONResponse(status_code=204,content=None)
    if rag is not None:
        @router.post("/knowledge/answer")
        async def answer_knowledge(request: Request):
            principal=await resolve_knowledge_principal(request);body=await request.json()
            try:result=await rag.answer(principal=principal,query=str(body.get("query") or ""),limit=int(body.get("limit",8)))
            except ValueError as exc:raise HTTPException(400,str(exc)) from exc
            return {"answer":result.answer,"citations":list(result.citations),"abstained":result.abstained,"reason":result.reason}
    @router.get("/knowledge/search")
    async def search_knowledge(request: Request,q: str,limit: int=8):
        principal=await resolve_knowledge_principal(request)
        try:hits=await knowledge.retrieve(principal=principal,query=q,limit=limit)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        return {"items":[{"citation_id":h.citation_id,"document_id":h.document_id,"document_version":h.document_version,"chunk_id":h.chunk_id,"title":h.title,"source_uri":h.source_uri,"page_number":h.page_number,"start_offset":h.start_offset,"end_offset":h.end_offset,"text":h.text,"score":h.score,"retrieval_trace":h.retrieval_trace} for h in hits]}
    @router.get("/memories")
    async def list_memories(request: Request,limit: int=100):
        principal=await resolve_memory_principal(request)
        try:records=await memory.retrieve(principal=principal,limit=limit)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        return {"items":[{"memory_id":r.candidate.memory_id,"scope":r.candidate.scope.value,"scope_id":r.candidate.scope_id,"content":r.candidate.content,"source_ref":r.candidate.source_ref,"confidence":r.candidate.confidence,"version":r.version,"status":r.status.value,"expires_at":r.candidate.expires_at.isoformat() if r.candidate.expires_at else None} for r in records]}
    @router.get("/memories/candidates")
    async def list_memory_candidates(request: Request):
        principal=await resolve_memory_principal(request)
        records=await memory.repository.list(principal.tenant_id,limit=1000)
        visible=[r for r in records if r.status.value in {"candidate","conflict"} and _memory_visible_for_review(r,principal)]
        return {"items":[{"memory_id":r.candidate.memory_id,"scope":r.candidate.scope.value,"scope_id":r.candidate.scope_id,"content":r.candidate.content,"source_ref":r.candidate.source_ref,"confidence":r.candidate.confidence,"conflict_key":r.candidate.conflict_key,"status":r.status.value,"version":r.version} for r in visible]}
    @router.post("/memories/conflicts/resolve")
    async def resolve_memory_conflict(request: Request):
        principal=await resolve_memory_principal(request);body=await request.json()
        try:record=await memory.resolve_conflict(principal=principal,conflict_key=str(body.get("conflict_key") or ""),selected_memory_id=str(body.get("selected_memory_id") or ""))
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        return {"memory_id":record.candidate.memory_id,"status":record.status.value,"version":record.version}
    @router.get("/memories/{memory_id}/history")
    async def memory_history(memory_id: str,request: Request):
        principal=await resolve_memory_principal(request)
        try:
            records=await memory.history(principal=principal,memory_id=memory_id)
        except PermissionError as exc:
            raise HTTPException(403,str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(404,str(exc)) from exc
        return {"items":[{"memory_id":r.candidate.memory_id,"version":r.version,"scope":r.candidate.scope.value,"scope_id":r.candidate.scope_id,"content":r.candidate.content,"source_ref":r.candidate.source_ref,"confidence":r.candidate.confidence,"status":r.status.value,"confirmed_by":r.confirmed_by,"recorded_at":r.updated_at.isoformat()} for r in records]}
    @router.post("/memories/{memory_id}/archive")
    async def archive_memory(memory_id: str,request: Request):
        principal=await resolve_memory_principal(request)
        try:archived=await memory.archive(principal=principal,memory_id=memory_id)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        if not archived:raise HTTPException(404,"active memory not found")
        return {"memory_id":memory_id,"status":"archived"}
    @router.get("/memories/export")
    async def export_memories(request: Request):
        principal=await resolve_memory_principal(request)
        return {"items":await memory.export(principal=principal)}
    if memory_extraction is not None:
        @router.post("/memories/extract")
        async def extract_memory_candidates(request: Request):
            principal=await resolve_memory_principal(request);body=await request.json()
            turn=MemoryTurn(
                tenant_id=principal.tenant_id,user_id=principal.user_id,
                source_ref=str(body.get("source_ref") or ""),
                user_message=str(body.get("user_message") or ""),
                assistant_message=str(body.get("assistant_message") or ""),
                agent_id=str(body["agent_id"]) if body.get("agent_id") else None,
                project_id=str(body["project_id"]) if body.get("project_id") else None,
            )
            try:records=await memory_extraction.propose_from_turn(principal=principal,turn=turn)
            except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
            except ValueError as exc:raise HTTPException(400,str(exc)) from exc
            return {"items":[{"memory_id":r.candidate.memory_id,"scope":r.candidate.scope.value,"scope_id":r.candidate.scope_id,"content":r.candidate.content,"confidence":r.candidate.confidence,"status":r.status.value} for r in records]}
    @router.post("/memories/candidates")
    async def propose_memory(request: Request):
        principal=await resolve_memory_principal(request);body=await request.json()
        try:scope=MemoryScope(body.get("scope","user"))
        except ValueError as exc:raise HTTPException(400,"invalid memory scope") from exc
        scope_id=str(body.get("scope_id") or (principal.user_id if scope is MemoryScope.USER else ""))
        if scope is MemoryScope.USER and scope_id!=principal.user_id:raise HTTPException(403,"cannot write another user's memory")
        if scope is MemoryScope.PROJECT and scope_id not in principal.manage_project_ids and "memory.manage" not in principal.permissions:raise HTTPException(403,"project memory.manage permission required")
        if scope is MemoryScope.AGENT and scope_id not in principal.manage_agent_ids and "memory.manage" not in principal.permissions:raise HTTPException(403,"agent memory.manage permission required")
        if scope is MemoryScope.TENANT and "memory.manage" not in principal.permissions:raise HTTPException(403,"memory.manage role required")
        expiry=body.get("expires_at")
        try:expires_at=datetime.fromisoformat(expiry) if expiry else None
        except ValueError as exc:raise HTTPException(400,"expires_at must be ISO-8601") from exc
        try:
            confidence=float(body.get("confidence",0.5))
            candidate=MemoryCandidate(tenant_id=principal.tenant_id,scope=scope,scope_id=scope_id,content=str(body.get("content") or ""),source_ref=str(body.get("source_ref") or ""),confidence=confidence,created_by=principal.user_id,expires_at=expires_at,conflict_key=str(body.get("conflict_key")) if body.get("conflict_key") else None)
            record=await memory.propose(candidate)
        except ValueError as exc:raise HTTPException(400,str(exc)) from exc
        return {"memory_id":record.candidate.memory_id,"status":record.status.value}
    @router.post("/memories/{memory_id}/confirm")
    async def confirm_memory(memory_id: str,request: Request):
        principal=await resolve_memory_principal(request);record=await memory.repository.get(principal.tenant_id,memory_id)
        if record is None:raise HTTPException(404,"memory candidate not found")
        if not _memory_visible_for_review(record,principal):raise HTTPException(403,"memory confirmation is not authorized")
        try:updated=await memory.confirm(tenant_id=principal.tenant_id,memory_id=memory_id,confirmer_id=principal.user_id)
        except ValueError as exc:raise HTTPException(409,str(exc)) from exc
        return {"memory_id":updated.candidate.memory_id,"status":updated.status.value,"version":updated.version}
    @router.patch("/memories/{memory_id}")
    async def correct_memory(memory_id: str,request: Request):
        principal=await resolve_memory_principal(request);body=await request.json()
        try:updated=await memory.correct(principal=principal,memory_id=memory_id,content=str(body.get("content") or ""),source_ref=str(body.get("source_ref") or ""),confidence=body.get("confidence"))
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        except ValueError as exc:raise HTTPException(404,str(exc)) from exc
        return {"memory_id":updated.candidate.memory_id,"version":updated.version,"status":updated.status.value}
    @router.delete("/memories/{memory_id}",status_code=204)
    async def forget_memory(memory_id: str,request: Request):
        principal=await resolve_memory_principal(request)
        try:deleted=await memory.forget(principal=principal,memory_id=memory_id)
        except PermissionError as exc:raise HTTPException(403,str(exc)) from exc
        if not deleted:raise HTTPException(404,"memory not found")
        return JSONResponse(status_code=204,content=None)
    return router
