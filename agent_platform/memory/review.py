"""Authorized, auditable review workflow for extracted memory candidates.

Applications should use MemoryCandidateWorkflow instead of calling the low-level
MemoryService.confirm/reject methods directly. Conflict candidates are resolved
through MemoryService.resolve_conflict, which requires an explicit selection.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol
from uuid import uuid4

from agent_platform.memory.service import (
    MemoryPrincipal,
    MemoryRecord,
    MemoryService,
    MemoryStatus,
)


class MemoryReviewDecision(str, Enum):
    CONFIRM = "confirm"
    REJECT = "reject"


@dataclass(frozen=True)
class MemoryReviewEvent:
    event_id: str
    tenant_id: str
    memory_id: str
    decision: MemoryReviewDecision
    reviewer_id: str
    source_ref: str
    candidate_version: int
    reviewer_note: str = ""
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryReviewAuditRepository(Protocol):
    async def append(self, event: MemoryReviewEvent) -> None: ...
    async def list_for_memory(
        self, tenant_id: str, memory_id: str
    ) -> tuple[MemoryReviewEvent, ...]: ...


class InMemoryMemoryReviewAuditRepository:
    """Test/local adapter; production deployments inject a durable append-only sink."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], list[MemoryReviewEvent]] = {}

    async def append(self, event: MemoryReviewEvent) -> None:
        key = (event.tenant_id, event.memory_id)
        events = self._events.setdefault(key, [])
        if any(item.event_id == event.event_id for item in events):
            return
        events.append(event)

    async def list_for_memory(
        self, tenant_id: str, memory_id: str
    ) -> tuple[MemoryReviewEvent, ...]:
        return tuple(self._events.get((tenant_id, memory_id), ()))


class MemoryCandidateWorkflow:
    """Scope-authorized candidate review with a separate audit trail."""

    def __init__(
        self,
        *,
        memory: MemoryService,
        audit: MemoryReviewAuditRepository,
    ) -> None:
        self.memory = memory
        self.audit = audit

    @staticmethod
    def _authorize(record: MemoryRecord, principal: MemoryPrincipal) -> None:
        candidate = record.candidate
        if candidate.tenant_id != principal.tenant_id:
            raise PermissionError("candidate belongs to another tenant")
        if not MemoryService._can_manage_scope(
            candidate.scope, candidate.scope_id, principal
        ):
            raise PermissionError("memory candidate review is not authorized")

    async def review(
        self,
        *,
        principal: MemoryPrincipal,
        memory_id: str,
        decision: MemoryReviewDecision,
        reviewer_note: str = "",
    ) -> MemoryRecord | bool:
        if not isinstance(decision, MemoryReviewDecision):
            raise ValueError("decision must be a MemoryReviewDecision")
        if not isinstance(reviewer_note, str) or len(reviewer_note) > 2000:
            raise ValueError("reviewer_note must be a string of at most 2000 characters")

        record = await self.memory.repository.get(principal.tenant_id, memory_id)
        if record is None:
            raise ValueError("memory candidate not found")
        self._authorize(record, principal)
        if record.status is MemoryStatus.CONFLICT:
            raise ValueError(
                "conflict candidates require explicit resolve_conflict selection"
            )
        retrying_rejection = (
            decision is MemoryReviewDecision.REJECT
            and record.status is MemoryStatus.DELETED
        )
        if record.status is not MemoryStatus.CANDIDATE and not retrying_rejection:
            raise ValueError("candidate has already been reviewed")

        event = MemoryReviewEvent(
            event_id=str(uuid4()),
            tenant_id=principal.tenant_id,
            memory_id=memory_id,
            decision=decision,
            reviewer_id=principal.user_id,
            source_ref=record.candidate.source_ref,
            candidate_version=record.version,
            reviewer_note=reviewer_note,
        )
        if decision is MemoryReviewDecision.CONFIRM:
            result: MemoryRecord | bool = await self.memory.confirm(
                memory_id=memory_id,
                principal=principal,
            )
        else:
            result = await self.memory.reject(
                memory_id=memory_id,
                principal=principal,
            )
        await self.audit.append(event)
        return result

    async def history(
        self, *, principal: MemoryPrincipal, memory_id: str
    ) -> tuple[MemoryReviewEvent, ...]:
        record = await self.memory.repository.get(principal.tenant_id, memory_id)
        if record is None:
            raise ValueError("memory not found")
        self._authorize(record, principal)
        return await self.audit.list_for_memory(principal.tenant_id, memory_id)
