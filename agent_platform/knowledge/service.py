"""Knowledge ingestion, connector sync and ACL-first hybrid retrieval."""
from __future__ import annotations

from typing import Protocol, Sequence

from agent_platform.knowledge.chunking import chunk_document, sha256_text
from agent_platform.knowledge.connectors import KnowledgeConnector
from agent_platform.knowledge.contracts import (
    Document,
    IngestResult,
    KnowledgePrincipal,
    RetrievalHit,
)
from agent_platform.knowledge.ingestion import ParserRegistry
from agent_platform.knowledge.repository import KnowledgeRepository
from agent_platform.observability.instrumentation import safe_metric, safe_observation, safe_update


class Embedder(Protocol):
    model_id: str
    async def embed_query(self,text: str)->Sequence[float]: ...
    async def embed_documents(self,texts: Sequence[str])->Sequence[Sequence[float]]: ...
class Reranker(Protocol):
    async def rerank(self,query: str,hits: Sequence[RetrievalHit])->Sequence[RetrievalHit]: ...
class QueryRewriter(Protocol):
    async def rewrite(self,query: str)->str: ...
class KnowledgeService:
    def __init__(self,repository: KnowledgeRepository,*,parsers: ParserRegistry|None=None,embedder: Embedder|None=None,reranker: Reranker|None=None,query_rewriter: QueryRewriter|None=None,max_upload_bytes: int=25*1024*1024,observer=None,metrics=None)->None:
        if (
            isinstance(max_upload_bytes, bool)
            or not isinstance(max_upload_bytes, int)
            or max_upload_bytes < 1024
        ):
            raise ValueError("max_upload_bytes must be an integer of at least 1024")
        self.repository=repository;self.parsers=parsers or ParserRegistry();self.embedder=embedder;self.reranker=reranker;self.query_rewriter=query_rewriter;self.max_upload_bytes=max_upload_bytes;self.observer=observer;self.metrics=metrics
        if embedder is not None and hasattr(repository,"embedding_model"):
            repository.embedding_model=embedder.model_id
    async def ingest(self,*,document: Document,content: bytes,max_chars: int=1200,overlap: int=160)->IngestResult:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1:
            raise ValueError("max_chars must be a positive integer")
        if isinstance(overlap, bool) or not isinstance(overlap, int) or overlap < 0:
            raise ValueError("overlap must be a non-negative integer")
        if overlap >= max_chars:
            raise ValueError("overlap must be smaller than max_chars")
        if len(content)>self.max_upload_bytes:
            raise ValueError("document exceeds configured upload size limit")
        from dataclasses import replace
        text=self.parsers.parse(content,document.media_type)
        digest=sha256_text(text)
        existing=await self.repository.get_document(document.tenant_id,document.document_id)
        if existing is not None:
            unchanged=(
                existing.content_hash==digest
                and existing.title==document.title
                and existing.source_uri==document.source_uri
                and existing.scope is document.scope
                and existing.project_id==document.project_id
                and existing.owner_user_id==document.owner_user_id
                and existing.media_type==document.media_type
                and existing.metadata==document.metadata
            )
            if unchanged:
                document=existing
            elif document.version<=existing.version:
                document=replace(document,version=existing.version+1)
        document=replace(document,content_hash=digest)
        chunks=chunk_document(document,text,max_chars=max_chars,overlap=overlap)
        created=await self.repository.save_document(document,chunks)
        # Deterministic chunk IDs let retries repair embeddings after a partial failure.
        if chunks and self.embedder is not None:
            save_embeddings=getattr(self.repository,"save_embeddings",None)
            if save_embeddings is None:raise RuntimeError("repository does not support embedding persistence")
            vectors=await self.embedder.embed_documents([chunk.text for chunk in chunks])
            if len(vectors)!=len(chunks):raise ValueError("embedding count must match chunks")
            await save_embeddings(tenant_id=document.tenant_id,chunks=chunks,vectors=vectors)
        return IngestResult(document=document,chunks=chunks,skipped_unchanged=not created)
    async def list_documents(self,*,principal: KnowledgePrincipal,limit: int=100):
        return await self.repository.list_documents(principal,limit=limit)
    @staticmethod
    def _can_manage_document(document: Document, principal: KnowledgePrincipal) -> bool:
        if document.scope.value == "private":
            return document.owner_user_id == principal.user_id or "knowledge.manage" in principal.permissions
        if document.scope.value == "project":
            return "knowledge.manage" in principal.permissions or document.project_id in principal.manage_project_ids
        return "knowledge.manage" in principal.permissions

    async def grant_document_access(
        self, *, principal: KnowledgePrincipal, document_id: str,
        principal_type: str, principal_id: str, permission: str = "read",
    ) -> None:
        if principal_type not in {"user", "role", "project"}:
            raise ValueError("principal_type must be user, role or project")
        if permission not in {"read", "manage"}:
            raise ValueError("permission must be read or manage")
        if not principal_id.strip():
            raise ValueError("principal_id must not be empty")
        document = await self.repository.get_document(principal.tenant_id, document_id)
        if document is None:
            raise ValueError("document not found")
        if not self._can_manage_document(document, principal):
            raise PermissionError("document ACL management is not authorized")
        await self.repository.grant_document_access(
            principal.tenant_id, document_id, principal_type, principal_id,
            permission, principal.user_id,
        )

    async def revoke_document_access(
        self, *, principal: KnowledgePrincipal, document_id: str,
        principal_type: str, principal_id: str, permission: str = "read",
    ) -> bool:
        if principal_type not in {"user", "role", "project"}:
            raise ValueError("principal_type must be user, role or project")
        if permission not in {"read", "manage"}:
            raise ValueError("permission must be read or manage")
        document = await self.repository.get_document(principal.tenant_id, document_id)
        if document is None:
            raise ValueError("document not found")
        if not self._can_manage_document(document, principal):
            raise PermissionError("document ACL management is not authorized")
        return await self.repository.revoke_document_access(
            principal.tenant_id, document_id, principal_type, principal_id, permission,
        )

    async def delete_document(self,*,principal: KnowledgePrincipal,document_id: str)->bool:
        document=await self.repository.get_document(principal.tenant_id,document_id)
        if document is None:
            return False
        if not self._can_manage_document(document, principal):
            raise PermissionError("document deletion is not authorized")
        return await self.repository.delete_document(principal.tenant_id,document_id)
    @staticmethod
    def _connector_document(connector: KnowledgeConnector, tenant_id: str, document: Document) -> Document:
        from dataclasses import replace
        if not connector.connector_id.strip():
            raise ValueError("connector_id must not be empty")
        if document.tenant_id != tenant_id:
            raise PermissionError("connector returned a document for another tenant")
        metadata = dict(document.metadata)
        owner = metadata.get("_knowledge_connector_id")
        if owner is not None and owner != connector.connector_id:
            raise PermissionError("document is already owned by another connector")
        metadata["_knowledge_connector_id"] = connector.connector_id
        return replace(document, metadata=metadata)

    async def _apply_connector_deletions(
        self, *, connector: KnowledgeConnector, tenant_id: str, document_ids: Sequence[str]
    ) -> int:
        deleted = 0
        for document_id in document_ids:
            if not isinstance(document_id, str) or not document_id.strip():
                raise ValueError("connector deletion IDs must be non-empty strings")
            document = await self.repository.get_document(tenant_id, document_id)
            # Replayed tombstones are idempotent.
            if document is None:
                continue
            if document.metadata.get("_knowledge_connector_id") != connector.connector_id:
                raise PermissionError("connector cannot delete a document it does not own")
            if await self.repository.delete_document(tenant_id, document_id):
                deleted += 1
        return deleted

    async def ingest_connector_page(self,*,connector: KnowledgeConnector,tenant_id: str,cursor: str|None=None,limit: int=100)->tuple[list[IngestResult],str|None,bool]:
        page=await connector.list_changed(tenant_id=tenant_id,cursor=cursor,limit=limit)
        results=[]
        for item in page.items:
            document=self._connector_document(connector,tenant_id,item.document)
            results.append(await self.ingest(document=document,content=item.content))
        await self._apply_connector_deletions(
            connector=connector, tenant_id=tenant_id,
            document_ids=page.deleted_document_ids,
        )
        return results,page.next_cursor,page.has_more
    async def sync_connector(self,*,connector: KnowledgeConnector,tenant_id: str,limit: int=100,max_pages: int=10)->dict[str,object]:
        if limit<1 or limit>500:
            raise ValueError("limit must be between 1 and 500")
        if max_pages<1 or max_pages>100:
            raise ValueError("max_pages must be between 1 and 100")
        cursor=await self.repository.get_connector_cursor(tenant_id,connector.connector_id)
        page_count=0
        document_count=0
        deleted_count=0
        while page_count<max_pages:
            page=await connector.list_changed(tenant_id=tenant_id,cursor=cursor,limit=limit)
            for item in page.items:
                document=self._connector_document(connector,tenant_id,item.document)
                await self.ingest(document=document,content=item.content)
                document_count+=1
            deleted_count+=await self._apply_connector_deletions(
                connector=connector, tenant_id=tenant_id,
                document_ids=page.deleted_document_ids,
            )
            if page.next_cursor is not None:
                if page.has_more and page.next_cursor==cursor:
                    raise ValueError("connector cursor did not advance while more pages remain")
                await self.repository.save_connector_cursor(tenant_id,connector.connector_id,page.next_cursor)
                cursor=page.next_cursor
            page_count+=1
            if not page.has_more:
                break
            if page.next_cursor is None:
                raise ValueError("connector must return next_cursor when has_more is true")
        return {"connector_id":connector.connector_id,"pages":page_count,"documents":document_count,"deleted_documents":deleted_count,"cursor":cursor,"has_more":page_count==max_pages and bool(page.has_more)}

    async def retrieve(self,*,principal: KnowledgePrincipal,query: str,limit: int=8)->list[RetrievalHit]:
        if not query.strip():raise ValueError("query must not be empty")
        if limit<1 or limit>50:raise ValueError("limit must be between 1 and 50")
        from time import monotonic
        started=monotonic()
        status="failed"
        metadata={"tenant_id":principal.tenant_id,"query_chars":len(query),"limit":limit}
        try:
            with safe_observation(
                self.observer,name="knowledge.retrieve",
                input={"query_chars":len(query),"limit":limit},metadata=metadata,
            ) as span:
                rewritten=await self.query_rewriter.rewrite(query) if self.query_rewriter else query
                if not rewritten.strip():raise ValueError("query rewriter returned an empty query")
                vector=await self.embedder.embed_query(rewritten) if self.embedder else None
                # Tenant and resource ACLs are applied inside the repository query before chunk bodies leave storage.
                hits=await self.repository.search(principal=principal,query=rewritten,query_vector=vector,limit=max(limit*4,20))
                if self.reranker and hits:hits=list(await self.reranker.rerank(query,hits))
                result=list(hits[:limit])
                status="succeeded"
                safe_update(span,output={"status":status,"result_count":len(result)})
                return result
        except Exception as exc:
            # Do not put the query or provider exception text into trace payloads.
            safe_update(span,output={"status":"failed","error_type":type(exc).__name__})
            raise
        finally:
            safe_metric(self.metrics,"increment","knowledge_retrieval_total",
                        labels={"component":"knowledge","status":status})
            safe_metric(self.metrics,"observe","knowledge_retrieval_duration_seconds",
                        monotonic()-started,labels={"component":"knowledge"})
