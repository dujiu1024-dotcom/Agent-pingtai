"""Knowledge ingestion, connector sync and ACL-first hybrid retrieval."""
from __future__ import annotations

import asyncio
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
        # PDF/Office parsing and synchronous OCR providers may perform CPU-heavy
        # work or bounded network polling. Keep that work off the async worker loop.
        text = await asyncio.to_thread(
            self.parsers.parse, content, document.media_type,
        )
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
