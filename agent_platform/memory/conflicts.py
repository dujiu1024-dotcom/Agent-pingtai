"""Version-aware, auditable memory conflict resolution."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from agent_platform.memory.service import (
    MemoryCandidate,
    MemoryPrincipal,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemoryStatus,
)


class MemoryConflictResolution(str, Enum):
    SELECT = "select"
    MERGE = "merge"


@dataclass(frozen=True)
class MemoryConflictSource:
    memory_id: str
    version: int
    source_ref: str


@dataclass(frozen=True)
class MemoryConflictDecision:
    decision_id: str
    tenant_id: str
    conflict_key: str
    scope: MemoryScope
    scope_id: str
    resolution: MemoryConflictResolution
    sources: tuple[MemoryConflictSource, ...]
    selected_memory_id: str
    resulting_memory_id: str
    resulting_version: int
    actor_id: str
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MemoryConflictAuditRepository:
    """Append-only audit contract; production adapters must persist atomically."""

    async def append(self, decision: MemoryConflictDecision) -> None:
        raise NotImplementedError

    async def list_for_conflict(
        self, tenant_id: str, conflict_key: str
    ) -> tuple[MemoryConflictDecision, ...]:
        raise NotImplementedError


class InMemoryMemoryConflictAuditRepository(MemoryConflictAuditRepository):
    """In-memory adapter for tests and local development."""

    def __init__(self) -> None:
        self._events: dict[tuple[str, str], list[MemoryConflictDecision]] = {}

    async def append(self, decision: MemoryConflictDecision) -> None:
        key = (decision.tenant_id, decision.conflict_key)
        events = self._events.setdefault(key, [])
        if any(item.decision_id == decision.decision_id for item in events):
            return
        events.append(decision)

    async def list_for_conflict(
        self, tenant_id: str, conflict_key: str
    ) -> tuple[MemoryConflictDecision, ...]:
        return tuple(self._events.get((tenant_id, conflict_key), ()))


class MemoryConflictResolver:
    """Resolve one scope-local conflict set with exact version provenance."""

    def __init__(
        self, *, memory: MemoryService, audit: MemoryConflictAuditRepository
    ) -> None:
        self.memory = memory
        self.audit = audit

    async def resolve(
        self,
        *,
        principal: MemoryPrincipal,
        conflict_key: str,
        selected_memory_id: str,
        resolution: MemoryConflictResolution = MemoryConflictResolution.SELECT,
        merged_content: str | None = None,
    ) -> MemoryRecord:
        if not isinstance(conflict_key, str) or not conflict_key.strip():
            raise ValueError("conflict_key must be a non-empty string")
        if not isinstance(selected_memory_id, str) or not selected_memory_id.strip():
            raise ValueError("selected_memory_id must be a non-empty string")
        if not isinstance(resolution, MemoryConflictResolution):
            raise ValueError("resolution must be a MemoryConflictResolution")
        if resolution is MemoryConflictResolution.MERGE:
            if not isinstance(merged_content, str) or not merged_content.strip():
                raise ValueError("merged_content is required for merge resolution")
        elif merged_content is not None:
            raise ValueError("merged_content is only valid for merge resolution")

        records = await self.memory.repository.list(principal.tenant_id, limit=5000)
        group = [
            record for record in records
            if record.candidate.conflict_key == conflict_key
            and record.status in {MemoryStatus.CONFLICT, MemoryStatus.ACTIVE}
        ]
        selected = next(
            (record for record in group if record.candidate.memory_id == selected_memory_id),
            None,
        )
        if selected is None:
            raise ValueError("selected memory is not part of this active conflict set")
        scope = selected.candidate.scope
        scope_id = selected.candidate.scope_id
        group = [
            record for record in group
            if record.candidate.scope is scope and record.candidate.scope_id == scope_id
        ]
        if len(group) < 2:
            raise ValueError("conflict set must contain at least two competing versions")
        if not MemoryService._can_manage_scope(scope, scope_id, principal):
            raise PermissionError("memory conflict management is not authorized")

        ordered = tuple(sorted(group, key=lambda item: item.candidate.memory_id))
        sources = tuple(
            MemoryConflictSource(
                memory_id=item.candidate.memory_id,
                version=item.version,
                source_ref=item.candidate.source_ref,
            )
            for item in ordered
        )
        decision_id = str(uuid4())
        resulting_id = selected_memory_id
        merged_candidate: MemoryCandidate | None = None
        if resolution is MemoryConflictResolution.MERGE:
            metadata = dict(selected.candidate.metadata)
            metadata["conflict_decision_id"] = decision_id
            metadata["merged_from"] = ",".join(
                f"{item.memory_id}@{item.version}" for item in sources
            )
            resulting_id = str(uuid4())
            # Keep the canonical source pointer owned by its source domain. The
            # decision audit below records every contributing source/version;
            # a synthetic memory-conflict URI is not a resolvable source record.
            metadata["merged_source_refs"] = ",".join(
                item.source_ref for item in sources
            )
            merged_candidate = replace(
                selected.candidate,
                memory_id=resulting_id,
                content=merged_content.strip(),
                source_ref=selected.candidate.source_ref,
                metadata=metadata,
            )

        # Write-ahead decision: if audit is unavailable, do not mutate memory state.
        decision = MemoryConflictDecision(
            decision_id=decision_id,
            tenant_id=principal.tenant_id,
            conflict_key=conflict_key,
            scope=scope,
            scope_id=scope_id,
            resolution=resolution,
            sources=sources,
            selected_memory_id=selected_memory_id,
            resulting_memory_id=resulting_id,
            resulting_version=(2 if resolution is MemoryConflictResolution.MERGE else selected.version + 1),
            actor_id=principal.user_id,
        )
        await self.audit.append(decision)
        if merged_candidate is not None:
            await self.memory.propose(merged_candidate)
        result = await self.memory.resolve_conflict(
            principal=principal,
            conflict_key=conflict_key,
            selected_memory_id=resulting_id,
        )
        return result

    async def history(
        self, *, principal: MemoryPrincipal, memory_id: str
    ) -> tuple[MemoryConflictDecision, ...]:
        record = await self.memory.repository.get(principal.tenant_id, memory_id)
        if record is None:
            raise ValueError("memory not found")
        if not MemoryService._can_manage_scope(
            record.candidate.scope, record.candidate.scope_id, principal
        ):
            raise PermissionError("memory conflict audit access is not authorized")
        if not record.candidate.conflict_key:
            raise ValueError("memory is not associated with a conflict set")
        return await self.audit.list_for_conflict(
            principal.tenant_id, record.candidate.conflict_key
        )
