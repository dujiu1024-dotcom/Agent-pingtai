"""Governed cross-session memory; candidate confirmation is required before retrieval."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Awaitable, Callable, Protocol, Sequence
from uuid import uuid4

from agent_platform.memory.boundaries import MemoryBoundaryPolicy


def utc_now()->datetime:return datetime.now(timezone.utc)
class MemoryScope(str,Enum): USER="user"; AGENT="agent"; PROJECT="project"; TENANT="tenant"
class MemoryStatus(str,Enum): CANDIDATE="candidate"; CONFLICT="conflict"; ACTIVE="active"; SUPERSEDED="superseded"; ARCHIVED="archived"; EXPIRED="expired"; DELETED="deleted"
@dataclass(frozen=True)
class MemoryPrincipal:
    tenant_id: str
    user_id: str
    project_ids: frozenset[str]=frozenset()
    agent_ids: frozenset[str]=frozenset()
    permissions: frozenset[str]=frozenset()
    manage_project_ids: frozenset[str]=frozenset()
    manage_agent_ids: frozenset[str]=frozenset()
@dataclass(frozen=True)
class MemoryCandidate:
    tenant_id: str; scope: MemoryScope; scope_id: str; content: str; source_ref: str; confidence: float; created_by: str
    expires_at: datetime|None=None; memory_id: str=field(default_factory=lambda:str(uuid4())); metadata: dict[str,str]=field(default_factory=dict); conflict_key: str|None=None; source_approved: bool=True
    def __post_init__(self)->None:
        required = (self.tenant_id, self.scope_id, self.content, self.source_ref, self.created_by, self.memory_id)
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise ValueError("memory identity and content fields must be non-empty strings")
        if not isinstance(self.scope, MemoryScope):
            raise ValueError("scope must be a MemoryScope")
        if self.source_ref.startswith(("knowledge://", "blackboard://", "session://", "memory://")):
            reference = MemoryBoundaryPolicy.validate_reference(
                self.source_ref, approved=self.source_approved,
            )
            if reference.tenant_id != self.tenant_id:
                raise ValueError("source reference tenant must match memory tenant")
        if (
            isinstance(self.confidence, bool)
            or not isinstance(self.confidence, (int, float))
            or not 0 <= self.confidence <= 1
            or not isfinite(self.confidence)
        ):
            raise ValueError("confidence must be a finite number between 0 and 1")
        if self.expires_at is not None and (
            not isinstance(self.expires_at, datetime)
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at must be timezone-aware")
        for bound in (self.applies_from, self.applies_until):
            if bound is not None and (
                not isinstance(bound, datetime)
                or bound.tzinfo is None
                or bound.utcoffset() is None
            ):
                raise ValueError("applicability bounds must be timezone-aware")
        if self.applies_from is not None and self.applies_until is not None and self.applies_from >= self.applies_until:
            raise ValueError("applies_from must be earlier than applies_until")
        if self.conflict_key is not None and (
            not isinstance(self.conflict_key, str) or not self.conflict_key.strip()
        ):
            raise ValueError("conflict_key must be a non-empty string when provided")
        if not isinstance(self.metadata, Mapping) or any(
            not isinstance(key, str) or not key.strip() or not isinstance(value, str)
            for key, value in self.metadata.items()
        ):
            raise ValueError("metadata must map non-empty string keys to string values")
@dataclass(frozen=True)
class MemoryRecord:
    candidate: MemoryCandidate; status: MemoryStatus; version: int=1; confirmed_by: str|None=None
    created_at: datetime=field(default_factory=utc_now); updated_at: datetime=field(default_factory=utc_now); deleted_at: datetime|None=None
class MemoryRepository(Protocol):
    async def put(self,record: MemoryRecord)->None:...
    async def get(self,tenant_id: str,memory_id: str)->MemoryRecord|None:...
    async def list(self,tenant_id: str,limit: int=1000)->Sequence[MemoryRecord]:...
    async def delete(self,tenant_id: str,memory_id: str,at: datetime)->bool:...
    async def list_versions(self,tenant_id: str,memory_id: str)->Sequence[MemoryRecord]:...
class InMemoryMemoryRepository:
    def __init__(self)->None:
        self.records:dict[tuple[str,str],MemoryRecord]={}
        self.versions:dict[tuple[str,str],list[MemoryRecord]]={}
    async def put(self,record: MemoryRecord)->None:
        key=(record.candidate.tenant_id,record.candidate.memory_id); current=self.records.get(key)
        if current is not None and record.version!=current.version+1:raise RuntimeError("memory version conflict")
        if current is None and record.version!=1:raise RuntimeError("memory version conflict")
        self.records[key]=record
        self.versions.setdefault(key,[]).append(record)
    async def get(self,tenant_id: str,memory_id: str)->MemoryRecord|None:return self.records.get((tenant_id,memory_id))
    async def list_versions(self,tenant_id: str,memory_id: str)->Sequence[MemoryRecord]:
        # In-memory history mirrors PostgreSQL's recorded_at semantics: each
        # immutable snapshot's creation timestamp is its version-recorded time.
        return tuple(
            replace(record, created_at=record.updated_at, updated_at=record.updated_at)
            for record in self.versions.get((tenant_id,memory_id),())
        )
    async def list(self,tenant_id: str,limit: int=1000)->Sequence[MemoryRecord]:return [r for (t,_),r in self.records.items() if t==tenant_id][:limit]
    async def delete(self,tenant_id: str,memory_id: str,at: datetime)->bool:
        key=(tenant_id,memory_id); rec=self.records.get(key)
        if rec is None or rec.status is MemoryStatus.DELETED:return False
        updated=replace(rec,status=MemoryStatus.DELETED,deleted_at=at,updated_at=at,version=rec.version+1)
        self.records[key]=updated
        self.versions.setdefault(key,[]).append(updated)
        return True
class MemoryService:
    def __init__(self,repository: MemoryRepository,*,on_delete: Callable[[str,str],Awaitable[None]]|None=None)->None:self.repository=repository;self.on_delete=on_delete
    async def propose(self,candidate: MemoryCandidate)->MemoryRecord:
        existing = await self.repository.get(candidate.tenant_id, candidate.memory_id)
        if existing is not None:
            if (existing.candidate.scope is candidate.scope
                and existing.candidate.scope_id == candidate.scope_id
                and existing.candidate.metadata.get('extraction') == 'single_turn'
                and candidate.metadata.get('extraction') == 'single_turn'):
                return existing
            raise ValueError('memory ID already exists')
        status=MemoryStatus.CANDIDATE
        if candidate.conflict_key:
            existing=await self.repository.list(candidate.tenant_id,limit=5000)
            conflict=any(r.status is MemoryStatus.ACTIVE and r.candidate.scope is candidate.scope and r.candidate.scope_id==candidate.scope_id and r.candidate.conflict_key==candidate.conflict_key and r.candidate.content!=candidate.content for r in existing)
            if conflict:status=MemoryStatus.CONFLICT
        rec=MemoryRecord(candidate=candidate,status=status)
        try:
            await self.repository.put(rec)
        except Exception:
            existing = await self.repository.get(candidate.tenant_id, candidate.memory_id)
            if (existing is not None
                and existing.candidate.scope is candidate.scope
                and existing.candidate.scope_id == candidate.scope_id
                and existing.candidate.metadata.get('extraction') == 'single_turn'
                and candidate.metadata.get('extraction') == 'single_turn'):
                return existing
            raise
        return rec
    async def confirm(
        self, *, memory_id: str, principal: MemoryPrincipal,
    ) -> MemoryRecord:
        rec = await self.repository.get(principal.tenant_id, memory_id)
        if rec is None or rec.status is not MemoryStatus.CANDIDATE:
            raise ValueError("candidate memory not found or already reviewed")
        if not self._can_manage_scope(
            rec.candidate.scope, rec.candidate.scope_id, principal,
        ):
            raise PermissionError("memory candidate confirmation is not authorized")
        updated = replace(
            rec, status=MemoryStatus.ACTIVE, confirmed_by=principal.user_id,
            updated_at=utc_now(), version=rec.version + 1,
        )
        await self.repository.put(updated)
        return updated

    async def reject(self, *, principal: MemoryPrincipal, memory_id: str) -> bool:
        rec = await self.repository.get(principal.tenant_id, memory_id)
        if rec is None or rec.status not in {
            MemoryStatus.CANDIDATE, MemoryStatus.DELETED,
        }:
            return False
        if not self._can_manage_scope(
            rec.candidate.scope, rec.candidate.scope_id, principal,
        ):
            raise PermissionError("memory candidate rejection is not authorized")
        if rec.status is MemoryStatus.CANDIDATE:
            now = utc_now()
            await self.repository.put(replace(
                rec, status=MemoryStatus.DELETED, deleted_at=now,
                updated_at=now, version=rec.version + 1,
            ))
        # Candidate rejection also removes derived index/cache state. Repeating
        # the operation retries cleanup after a transient downstream failure.
        if self.on_delete:
            await self.on_delete(principal.tenant_id, memory_id)
        return True
    @staticmethod
    def _can_manage_scope(scope: MemoryScope, scope_id: str, principal: MemoryPrincipal) -> bool:
        if scope is MemoryScope.USER:
            return scope_id == principal.user_id or "memory.manage" in principal.permissions
        if scope is MemoryScope.PROJECT:
            return (
                scope_id in principal.manage_project_ids
                or "memory.manage" in principal.permissions
            )
        if scope is MemoryScope.AGENT:
            return (
                scope_id in principal.manage_agent_ids
                or "memory.manage" in principal.permissions
            )
        return "memory.manage" in principal.permissions

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
        if not self._can_manage_scope(c.scope, c.scope_id, principal):
            raise PermissionError("memory correction is not authorized")
        candidate=replace(c,content=content,source_ref=source_ref,confidence=c.confidence if confidence is None else confidence )
        updated=replace(rec,candidate=candidate,version=rec.version+1,confirmed_by=principal.user_id,updated_at=utc_now())
        await self.repository.put(updated)
        return updated
    async def export(self,*,principal: MemoryPrincipal)->list[dict[str,object]]:
        """Export every non-deleted memory the principal is allowed to manage.

        Retrieval intentionally returns active memories only. A data export is
        broader: candidates and archived/expired/superseded records are still
        part of the subject's data and must not silently disappear from export.
        """
        records = await self.repository.list(principal.tenant_id, limit=5000)
        visible = [
            record for record in records
            if record.status is not MemoryStatus.DELETED
            and record.deleted_at is None
            and self._can_manage_scope(
                record.candidate.scope, record.candidate.scope_id, principal,
            )
        ]
        visible.sort(key=lambda record: (
            record.updated_at, record.candidate.memory_id,
        ), reverse=True)
        return [
            {
                "memory_id": record.candidate.memory_id,
                "scope": record.candidate.scope.value,
                "scope_id": record.candidate.scope_id,
                "content": record.candidate.content,
                "source_ref": record.candidate.source_ref,
                "confidence": record.candidate.confidence,
                "version": record.version,
                "status": record.status.value,
                "created_by": record.candidate.created_by,
                "confirmed_by": record.confirmed_by,
                "metadata": dict(record.candidate.metadata),
                "expires_at": (
                    record.candidate.expires_at.isoformat()
                    if record.candidate.expires_at else None
                ),
                "applies_from": (
                    record.candidate.applies_from.isoformat()
                    if record.candidate.applies_from else None
                ),
                "applies_until": (
                    record.candidate.applies_until.isoformat()
                    if record.candidate.applies_until else None
                ),
                "created_at": record.created_at.isoformat(),
                "updated_at": record.updated_at.isoformat(),
            }
            for record in visible
        ]
    async def history(self,*,principal: MemoryPrincipal,memory_id: str)->list[MemoryRecord]:
        record=await self.repository.get(principal.tenant_id,memory_id)
        if record is None:
            raise ValueError("memory not found")
        if not self._can_manage_scope(record.candidate.scope,record.candidate.scope_id,principal):
            raise PermissionError("memory history access is not authorized")
        list_versions=getattr(self.repository,"list_versions",None)
        if list_versions is None:
            raise RuntimeError("memory repository does not support version history")
        return list(await list_versions(principal.tenant_id,memory_id))

    async def resolve_conflict(self,*,principal: MemoryPrincipal,conflict_key: str,selected_memory_id: str)->MemoryRecord:
        records=await self.repository.list(principal.tenant_id,limit=5000)
        candidates=[r for r in records if r.candidate.conflict_key==conflict_key and r.status in {MemoryStatus.CONFLICT,MemoryStatus.ACTIVE,MemoryStatus.SUPERSEDED}]
        selected=next((r for r in candidates if r.candidate.memory_id==selected_memory_id),None)
        if selected is None:
            raise ValueError("selected memory is not part of this conflict set")
        if not self._can_manage_scope(
            selected.candidate.scope, selected.candidate.scope_id, principal,
        ):
            raise PermissionError("memory conflict management is not authorized")
        group=[r for r in candidates if r.candidate.scope is selected.candidate.scope and r.candidate.scope_id==selected.candidate.scope_id]
        now=utc_now()
        for record in group:
            target=MemoryStatus.ACTIVE if record.candidate.memory_id==selected_memory_id else MemoryStatus.SUPERSEDED
            if record.status is not target:
                await self.repository.put(replace(record,status=target,confirmed_by=principal.user_id,updated_at=now,version=record.version+1))
            # Superseded memories are no longer retrievable. Retry cleanup on
            # repeated conflict resolution if an earlier cache/index purge failed.
            if target is MemoryStatus.SUPERSEDED and self.on_delete:
                await self.on_delete(principal.tenant_id,record.candidate.memory_id)
        return await self.repository.get(principal.tenant_id,selected_memory_id)
    async def archive(self,*,principal: MemoryPrincipal,memory_id: str)->bool:
        record=await self.repository.get(principal.tenant_id,memory_id)
        if record is None:
            return False
        if not self._can_manage_scope(
            record.candidate.scope, record.candidate.scope_id, principal,
        ):
            raise PermissionError("memory archive is not authorized")
        if record.status is MemoryStatus.ACTIVE:
            await self.repository.put(replace(record,status=MemoryStatus.ARCHIVED,updated_at=utc_now(),version=record.version+1))
        elif record.status is not MemoryStatus.ARCHIVED:
            return False
        # Archive is a visibility-removing lifecycle transition. Re-running
        # this operation retries an idempotent index/cache purge after outages.
        if self.on_delete:
            await self.on_delete(principal.tenant_id,memory_id)
        return True
    async def expire_due(self,*,tenant_id: str,now: datetime|None=None)->int:
        at=now or utc_now()
        records=await self.repository.list(tenant_id,limit=5000)
        expired=0
        for rec in records:
            due = rec.candidate.expires_at is not None and rec.candidate.expires_at <= at
            if rec.status is MemoryStatus.ACTIVE and due:
                await self.repository.put(replace(rec,status=MemoryStatus.EXPIRED,updated_at=at,version=rec.version+1))
                expired+=1
            # Expiry is a visibility boundary just like deletion. Re-run the
            # idempotent cleanup hook for already-expired records so a prior
            # cache/index outage cannot leave expired memory retrievable.
            elif rec.status is MemoryStatus.EXPIRED and due and self.on_delete:
                await self.on_delete(tenant_id,rec.candidate.memory_id)
            if rec.status is MemoryStatus.ACTIVE and due and self.on_delete:
                await self.on_delete(tenant_id,rec.candidate.memory_id)
        return expired
    async def forget(self,*,principal: MemoryPrincipal,memory_id: str)->bool:
        rec=await self.repository.get(principal.tenant_id,memory_id)
        if rec is None:return False
        c=rec.candidate
        if not self._can_manage_scope(c.scope, c.scope_id, principal):
            raise PermissionError("memory deletion is not authorized")
        already_deleted = rec.status is MemoryStatus.DELETED
        deleted=await self.repository.delete(principal.tenant_id,memory_id,utc_now())
        # Deletion propagation is retryable: the soft-deleted row remains as
        # an audit marker, so a retry must re-run cache/index cleanup even when
        # the repository reports that no new state transition was necessary.
        latest=await self.repository.get(principal.tenant_id,memory_id)
        if self.on_delete and (deleted or (latest is not None and latest.status is MemoryStatus.DELETED)):
            await self.on_delete(principal.tenant_id,memory_id)
        # Treat an already-completed logical deletion as success. This makes
        # DELETE idempotent and lets a caller retry after transient cleanup failure.
        return deleted or already_deleted or (
            latest is not None and latest.status is MemoryStatus.DELETED
        )
