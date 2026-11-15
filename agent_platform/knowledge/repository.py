"""Tenant- and ACL-scoped knowledge repository ports plus in-memory adapter."""
from __future__ import annotations
from typing import Protocol, Sequence
from agent_platform.knowledge.contracts import Document, DocumentChunk, KnowledgePrincipal, RetrievalHit
class KnowledgeRepository(Protocol):
    async def save_document(self, document: Document, chunks: Sequence[DocumentChunk]) -> bool: ...
    async def get_document(self, tenant_id: str, document_id: str, version: int|None=None) -> Document|None: ...
    async def list_documents(self, principal: KnowledgePrincipal, limit: int=100) -> list[Document]: ...
    async def list_documents(self, principal: KnowledgePrincipal, limit: int=100) -> list[Document]:
        if limit < 1 or limit > 500:
            raise ValueError("limit must be between 1 and 500")
        visible=[]
        for doc in self.documents.values():
            if doc.tenant_id != principal.tenant_id or doc.deleted_at is not None:
                continue
            if doc.scope.value == "private" and doc.owner_user_id != principal.user_id:
                continue
            if doc.scope.value == "project" and doc.project_id not in principal.project_ids:
                continue
            visible.append(doc)
        return sorted(visible,key=lambda d:d.created_at,reverse=True)[:limit]
    async def search(self, *, principal: KnowledgePrincipal, query: str, query_vector: Sequence[float]|None, limit: int) -> list[RetrievalHit]: ...
    async def delete_document(self, tenant_id: str, document_id: str) -> bool: ...
class InMemoryKnowledgeRepository:
    """Test adapter; production uses PostgreSQL ACL predicates before returning chunk text."""
    def __init__(self) -> None: self.documents: dict[tuple[str,str,int],Document]={}; self.chunks: dict[tuple[str,str,int],list[DocumentChunk]]={}
    async def save_document(self, document: Document, chunks: Sequence[DocumentChunk]) -> bool:
        key=(document.tenant_id,document.document_id,document.version)
        if key in self.documents: return False
        self.documents[key]=document; self.chunks[key]=list(chunks); return True
    async def get_document(self, tenant_id: str, document_id: str, version: int|None=None) -> Document|None:
        docs=[d for (t,i,v),d in self.documents.items() if t==tenant_id and i==document_id and (version is None or v==version) and d.deleted_at is None]
        return max(docs,key=lambda d:d.version) if docs else None
    async def search(self, *, principal: KnowledgePrincipal, query: str, query_vector: Sequence[float]|None, limit: int) -> list[RetrievalHit]:
        terms={t.casefold() for t in query.split() if t}; hits=[]
        for key,doc in self.documents.items():
            if doc.tenant_id!=principal.tenant_id or doc.deleted_at is not None: continue
            if doc.scope.value=="private" and doc.owner_user_id!=principal.user_id: continue
            if doc.scope.value=="project" and doc.project_id not in principal.project_ids: continue
            for chunk in self.chunks.get(key,[]):
                body=chunk.text.casefold(); score=sum(1 for term in terms if term in body)/max(1,len(terms))
                if score>0: hits.append(RetrievalHit(document_id=doc.document_id,document_version=doc.version,chunk_id=chunk.chunk_id,text=chunk.text,source_uri=doc.source_uri,title=doc.title,score=score,page_number=chunk.page_number,start_offset=chunk.start_offset,end_offset=chunk.end_offset,retrieval_trace={"strategy":"lexical-test-adapter"}))
        return sorted(hits,key=lambda h:h.score,reverse=True)[:limit]
    async def delete_document(self, tenant_id: str, document_id: str) -> bool:
        from dataclasses import replace
        from datetime import datetime,timezone
        found=False
        for key,doc in list(self.documents.items()):
            if key[0]==tenant_id and key[1]==document_id and doc.deleted_at is None:
                self.documents[key]=replace(doc,deleted_at=datetime.now(timezone.utc)); self.chunks.pop(key,None); found=True
        return found
