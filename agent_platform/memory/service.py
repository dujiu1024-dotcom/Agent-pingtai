"""Governed cross-session memory; candidate confirmation is required before retrieval."""
from __future__ import annotations
from dataclasses import dataclass,field,replace
from datetime import datetime,timezone
from enum import Enum
from typing import Protocol,Sequence,Awaitable,Callable
from uuid import uuid4

def utc_now()->datetime:return datetime.now(timezone.utc)
class MemoryScope(str,Enum): USER="user"; AGENT="agent"; PROJECT="project"; TENANT="tenant"
class MemoryStatus(str,Enum): CANDIDATE="candidate"; ACTIVE="active"; EXPIRED="expired"; DELETED="deleted"
@dataclass(frozen=True)
class MemoryPrincipal:
    tenant_id: str
    user_id: str
    project_ids: frozenset[str]=frozenset()
    agent_ids: frozenset[str]=frozenset()
    roles: frozenset[str]=frozenset()
@dataclass(frozen=True)
class MemoryCandidate:
    tenant_id: str; scope: MemoryScope; scope_id: str; content: str; source_ref: str; confidence: float; created_by: str
    expires_at: datetime|None=None; memory_id: str=field(default_factory=lambda:str(uuid4())); metadata: dict[str,str]=field(default_factory=dict)
    def __post_init__(self)->None:
        if not all(x.strip() for x in (self.tenant_id,self.scope_id,self.content,self.source_ref,self.created_by)):raise ValueError("memory fields must not be empty")
        if not 0<=self.confidence<=1:raise ValueError("confidence must be between 0 and 1")
@dataclass(frozen=True)
class MemoryRecord:
    candidate: MemoryCandidate; status: MemoryStatus; version: int=1; confirmed_by: str|None=None
    created_at: datetime=field(default_factory=utc_now); updated_at: datetime=field(default_factory=utc_now); deleted_at: datetime|None=None
class MemoryRepository(Protocol):
    async def put(self,record: MemoryRecord)->None:...
    async def get(self,tenant_id: str,memory_id: str)->MemoryRecord|None:...
    async def list(self,tenant_id: str,limit: int=1000)->Sequence[MemoryRecord]:...
    async def delete(self,tenant_id: str,memory_id: str,at: datetime)->bool:...
class InMemoryMemoryRepository:
    def __init__(self)->None:self.records:dict[tuple[str,str],MemoryRecord]={}
    async def put(self,record: MemoryRecord)->None:
        key=(record.candidate.tenant_id,record.candidate.memory_id); current=self.records.get(key)
        if current is not None and record.version!=current.version+1:raise RuntimeError("memory version conflict")
        if current is None and record.version!=1:raise RuntimeError("memory version conflict")
        self.records[key]=record
    async def get(self,tenant_id: str,memory_id: str)->MemoryRecord|None:return self.records.get((tenant_id,memory_id))
    async def list(self,tenant_id: str,limit: int=1000)->Sequence[MemoryRecord]:return [r for (t,_),r in self.records.items() if t==tenant_id][:limit]
    async def delete(self,tenant_id: str,memory_id: str,at: datetime)->bool:
        key=(tenant_id,memory_id); rec=self.records.get(key)
        if rec is None or rec.status is MemoryStatus.DELETED:return False
        self.records[key]=replace(rec,status=MemoryStatus.DELETED,deleted_at=at,updated_at=at,version=rec.version+1);return True
class MemoryService:
    def __init__(self,repository: MemoryRepository,*,on_delete: Callable[[str,str],Awaitable[None]]|None=None)->None:self.repository=repository;self.on_delete=on_delete
    async def propose(self,candidate: MemoryCandidate)->MemoryRecord:
        rec=MemoryRecord(candidate=candidate,status=MemoryStatus.CANDIDATE);await self.repository.put(rec);return rec
    async def confirm(self,*,tenant_id: str,memory_id: str,confirmer_id: str)->MemoryRecord:
        rec=await self.repository.get(tenant_id,memory_id)
        if rec is None or rec.status is not MemoryStatus.CANDIDATE:raise ValueError("candidate memory not found or already reviewed")
        updated=replace(rec,status=MemoryStatus.ACTIVE,confirmed_by=confirmer_id,updated_at=utc_now(),version=rec.version+1);await self.repository.put(updated);return updated
    async def reject(self,*,tenant_id: str,memory_id: str)->bool:
        rec=await self.repository.get(tenant_id,memory_id)
        if rec is None or rec.status is not MemoryStatus.CANDIDATE:return False
        await self.repository.put(replace(rec,status=MemoryStatus.DELETED,deleted_at=utc_now(),updated_at=utc_now(),version=rec.version+1));return True
    @staticmethod
    def _visible(rec: MemoryRecord,p: MemoryPrincipal)->bool:
        c=rec.candidate
        if c.tenant_id!=p.tenant_id or rec.status is not MemoryStatus.ACTIVE or rec.deleted_at:return False
        if c.expires_at and c.expires_at<=utc_now():return False
        return ((c.scope is MemoryScope.USER and c.scope_id==p.user_id) or (c.scope is MemoryScope.AGENT and c.scope_id in p.agent_ids) or (c.scope is MemoryScope.PROJECT and c.scope_id in p.project_ids) or c.scope is MemoryScope.TENANT)
    async def retrieve(self,*,principal: MemoryPrincipal,scope: MemoryScope|None=None,limit: int=100)->list[MemoryRecord]:
        if limit<1 or limit>1000:raise ValueError("limit must be between 1 and 1000")
        records=await self.repository.list(principal.tenant_id,limit=1000); visible=[r for r in records if self._visible(r,principal) and (scope is None or r.candidate.scope is scope)]
        return sorted(visible,key=lambda r:(r.candidate.confidence,r.updated_at),reverse=True)[:limit]
    async def correct(self,*,principal: MemoryPrincipal,memory_id: str,content: str,source_ref: str,confidence: float|None=None)->MemoryRecord:
        rec=await self.repository.get(principal.tenant_id,memory_id)
        if rec is None or rec.status is not MemoryStatus.ACTIVE:raise ValueError("active memory not found")
        c=rec.candidate
        owns_user=(c.scope is MemoryScope.USER and c.scope_id==principal.user_id)
        manages_shared=(c.scope is not MemoryScope.USER and "memory.manage" in principal.roles)
        if not (owns_user or manages_shared):raise PermissionError("memory correction is not authorized")
        candidate=replace(c,content=content,source_ref=source_ref,confidence=c.confidence if confidence is None else confidence,created_by=principal.user_id)
        updated=replace(rec,candidate=candidate,version=rec.version+1,confirmed_by=principal.user_id,updated_at=utc_now())
        await self.repository.put(updated)
        return updated
    async def export(self,*,principal: MemoryPrincipal)->list[dict[str,object]]:
        records=await self.retrieve(principal=principal,limit=1000)
        return [{"memory_id":r.candidate.memory_id,"scope":r.candidate.scope.value,"scope_id":r.candidate.scope_id,"content":r.candidate.content,"source_ref":r.candidate.source_ref,"confidence":r.candidate.confidence,"version":r.version,"expires_at":r.candidate.expires_at.isoformat() if r.candidate.expires_at else None,"updated_at":r.updated_at.isoformat()} for r in records]
    async def expire_due(self,*,tenant_id: str,now: datetime|None=None)->int:
        at=now or utc_now()
        records=await self.repository.list(tenant_id,limit=5000)
        expired=0
        for rec in records:
            if rec.status is MemoryStatus.ACTIVE and rec.candidate.expires_at and rec.candidate.expires_at<=at:
                await self.repository.put(replace(rec,status=MemoryStatus.EXPIRED,updated_at=at,version=rec.version+1))
                expired+=1
        return expired
    async def forget(self,*,principal: MemoryPrincipal,memory_id: str)->bool:
        rec=await self.repository.get(principal.tenant_id,memory_id)
        if rec is None:return False
        c=rec.candidate
        owns_user=(c.scope is MemoryScope.USER and c.scope_id==principal.user_id)
        manages_shared=(c.scope is not MemoryScope.USER and "memory.manage" in principal.roles)
        if not (owns_user or manages_shared):raise PermissionError("memory deletion is not authorized")
        deleted=await self.repository.delete(principal.tenant_id,memory_id,utc_now())
        if deleted and self.on_delete:await self.on_delete(principal.tenant_id,memory_id)
        return deleted
