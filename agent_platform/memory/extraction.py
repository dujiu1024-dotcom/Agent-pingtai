"""Candidate-only extraction from a single interaction turn.

The extractor never receives or persists an entire conversation transcript.
Every output remains a candidate until an authorized reviewer confirms it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence

from agent_platform.memory.service import (
    MemoryCandidate,
    MemoryPrincipal,
    MemoryRecord,
    MemoryScope,
    MemoryService,
)


@dataclass(frozen=True)
class MemoryTurn:
    tenant_id: str
    user_id: str
    source_ref: str
    user_message: str
    assistant_message: str = ""
    agent_id: str | None = None
    project_id: str | None = None


@dataclass(frozen=True)
class MemoryDraft:
    scope: MemoryScope
    scope_id: str
    content: str
    confidence: float
    expires_at: datetime | None = None
    conflict_key: str | None = None


class MemoryExtractor(Protocol):
    async def extract(self, *, turn: MemoryTurn) -> Sequence[MemoryDraft]: ...


class MemoryExtractionService:
    def __init__(self, *, memory: MemoryService, extractor: MemoryExtractor) -> None:
        self.memory = memory
        self.extractor = extractor

    async def propose_from_turn(
        self, *, principal: MemoryPrincipal, turn: MemoryTurn
    ) -> list[MemoryRecord]:
        if turn.tenant_id != principal.tenant_id or turn.user_id != principal.user_id:
            raise PermissionError("memory turn does not belong to the authenticated principal")
        if not turn.source_ref.strip():
            raise ValueError("source_ref is required for memory provenance")
        if not turn.user_message.strip() and not turn.assistant_message.strip():
            raise ValueError("memory extraction requires a non-empty interaction turn")
        if len(turn.user_message) + len(turn.assistant_message) > 20000:
            raise ValueError("interaction turn exceeds the extraction size limit")
        if turn.agent_id and turn.agent_id not in principal.agent_ids:
            raise PermissionError("agent context is not authorized")
        if turn.project_id and turn.project_id not in principal.project_ids:
            raise PermissionError("project context is not authorized")

        drafts = await self.extractor.extract(turn=turn)
        if len(drafts) > 50:
            raise ValueError("extractor returned too many memory candidates")
        records = []
        for draft in drafts:
            if draft.scope is MemoryScope.USER:
                if draft.scope_id != principal.user_id:
                    raise PermissionError("user memory scope must match the principal")
            elif draft.scope is MemoryScope.AGENT:
                if (
                    draft.scope_id not in principal.manage_agent_ids
                    and "memory.manage" not in principal.roles
                ):
                    raise PermissionError("agent memory management is not authorized")
            elif draft.scope is MemoryScope.PROJECT:
                if (
                    draft.scope_id not in principal.manage_project_ids
                    and "memory.manage" not in principal.roles
                ):
                    raise PermissionError("project memory management is not authorized")
            elif draft.scope is MemoryScope.TENANT:
                if "memory.manage" not in principal.roles:
                    raise PermissionError("tenant memory requires memory.manage")
            candidate = MemoryCandidate(
                tenant_id=principal.tenant_id,
                scope=draft.scope,
                scope_id=draft.scope_id,
                content=draft.content,
                source_ref=turn.source_ref,
                confidence=draft.confidence,
                created_by=principal.user_id,
                expires_at=draft.expires_at,
                conflict_key=draft.conflict_key,
                metadata={
                    "extraction": "single_turn",
                    "agent_id": turn.agent_id or "",
                    "project_id": turn.project_id or "",
                },
            )
            records.append(await self.memory.propose(candidate))
        return records
