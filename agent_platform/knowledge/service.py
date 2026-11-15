"""Knowledge ingestion, connector sync and ACL-first hybrid retrieval."""
from __future__ import annotations
from typing import Protocol,Sequence
from agent_platform.knowledge.chunking import chunk_document,sha256_text
from agent_platform.knowledge.connectors import KnowledgeConnector
from agent_platform.knowledge.contracts import Document,IngestResult,KnowledgePrincipal,RetrievalHit
from agent_platform.knowledge.ingestion import ParserRegistry
from agent_platform.knowledge.repository import KnowledgeRepository
class Embedder(Protocol):
    model_id: str
    async def embed_query(self,text: str)->Sequence[float]: ...
    async def embed_documents(self,texts: Sequence[str])->Sequence[Sequence[float]]: ...
class Reranker(Protocol):
    async def rerank(self,query: str,hits: Sequence[RetrievalHit])->Sequence[RetrievalHit]: ...
class QueryRewriter(Protocol):
    async def rewrite(self,query: str)->str: ...
class KnowledgeService:
    def __init__(self,repository: KnowledgeRepository,*,parsers: ParserRegistry|None=None,embedder: Embedder|None=None,reranker: Reranker|None=None,query_rewriter: QueryRewriter|None=None,max_upload_bytes: int=25*1024*1024)->None:
        if max_upload_bytes<1024:
            raise ValueError("max_upload_bytes must be at least 1024")
        self.repository=repository;self.parsers=parsers or ParserRegistry();self.embedder=embedder;self.reranker=reranker;self.query_rewriter=query_rewriter;self.max_upload_bytes=max_upload_bytes
        if embedder is not None and hasattr(repository,"embedding_model"):
            repository.embedding_model=embedder.model_id
    async def ingest(self,*,document: Document,content: bytes,max_chars: int=1200,overlap: int=160)->IngestResult:
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
    async def delete_document(self,*,principal: KnowledgePrincipal,document_id: str)->bool:
        document=await self.repository.get_document(principal.tenant_id,document_id)
        if document is None:
            return False
        owns_private=document.scope.value=="private" and document.owner_user_id==principal.user_id
        manages_shared="knowledge.manage" in principal.permissions
        manages_project=(document.scope.value=="project" and document.project_id in principal.manage_project_ids)
        if not (owns_private or manages_shared or manages_project):
            raise PermissionError("document deletion is not authorized")
        return await self.repository.delete_document(principal.tenant_id,document_id)
    async def ingest_connector_page(self,*,connector: KnowledgeConnector,tenant_id: str,cursor: str|None=None,limit: int=100)->tuple[list[IngestResult],str|None,bool]:
        page=await connector.list_changed(tenant_id=tenant_id,cursor=cursor,limit=limit)
        results=[]
        for item in page.items:
            if item.document.tenant_id!=tenant_id:raise PermissionError("connector returned a document for another tenant")
            results.append(await self.ingest(document=item.document,content=item.content))
        return results,page.next_cursor,page.has_more
    async def sync_connector(self,*,connector: KnowledgeConnector,tenant_id: str,limit: int=100,max_pages: int=10)->dict[str,object]:
        if limit<1 or limit>500:
            raise ValueError("limit must be between 1 and 500")
        if max_pages<1 or max_pages>100:
            raise ValueError("max_pages must be between 1 and 100")
        cursor=await self.repository.get_connector_cursor(tenant_id,connector.connector_id)
        page_count=0
        document_count=0
        while page_count<max_pages:
            page=await connector.list_changed(tenant_id=tenant_id,cursor=cursor,limit=limit)
            for item in page.items:
                if item.document.tenant_id!=tenant_id:
                    raise PermissionError("connector returned a document for another tenant")
                await self.ingest(document=item.document,content=item.content)
                document_count+=1
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
        return {"connector_id":connector.connector_id,"pages":page_count,"documents":document_count,"cursor":cursor,"has_more":page_count==max_pages and bool(page.has_more)}

    async def retrieve(self,*,principal: KnowledgePrincipal,query: str,limit: int=8)->list[RetrievalHit]:
        if not query.strip():raise ValueError("query must not be empty")
        if limit<1 or limit>50:raise ValueError("limit must be between 1 and 50")
        rewritten=await self.query_rewriter.rewrite(query) if self.query_rewriter else query
        if not rewritten.strip():raise ValueError("query rewriter returned an empty query")
        vector=await self.embedder.embed_query(rewritten) if self.embedder else None
        # Tenant and resource ACLs are applied inside the repository query before chunk bodies leave storage.
        hits=await self.repository.search(principal=principal,query=rewritten,query_vector=vector,limit=max(limit*4,20))
        if self.reranker and hits:hits=list(await self.reranker.rerank(query,hits))
        return list(hits[:limit])
