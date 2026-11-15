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
    def __init__(self,repository: KnowledgeRepository,*,parsers: ParserRegistry|None=None,embedder: Embedder|None=None,reranker: Reranker|None=None,query_rewriter: QueryRewriter|None=None)->None:
        self.repository=repository;self.parsers=parsers or ParserRegistry();self.embedder=embedder;self.reranker=reranker;self.query_rewriter=query_rewriter
    async def ingest(self,*,document: Document,content: bytes,max_chars: int=1200,overlap: int=160)->IngestResult:
        text=self.parsers.parse(content,document.media_type);digest=sha256_text(text)
        if document.content_hash!=digest:
            from dataclasses import replace
            document=replace(document,content_hash=digest)
        chunks=chunk_document(document,text,max_chars=max_chars,overlap=overlap)
        created=await self.repository.save_document(document,chunks)
        if created and chunks and self.embedder is not None:
            save_embeddings=getattr(self.repository,"save_embeddings",None)
            if save_embeddings is None:raise RuntimeError("repository does not support embedding persistence")
            vectors=await self.embedder.embed_documents([chunk.text for chunk in chunks])
            if len(vectors)!=len(chunks):raise ValueError("embedding count must match chunk count")
            await save_embeddings(tenant_id=document.tenant_id,chunks=chunks,vectors=vectors)
        return IngestResult(document=document,chunks=chunks,skipped_unchanged=not created)
    async def ingest_connector_page(self,*,connector: KnowledgeConnector,tenant_id: str,cursor: str|None=None,limit: int=100)->tuple[list[IngestResult],str|None,bool]:
        page=await connector.list_changed(tenant_id=tenant_id,cursor=cursor,limit=limit)
        results=[]
        for item in page.items:
            if item.document.tenant_id!=tenant_id:raise PermissionError("connector returned a document for another tenant")
            results.append(await self.ingest(document=item.document,content=item.content))
        return results,page.next_cursor,page.has_more
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
