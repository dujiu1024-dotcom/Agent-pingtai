"""Candidate-only extraction from a single interaction turn.

The extractor never receives or persists an entire conversation transcript.
Every output remains a candidate until an authorized reviewer confirms it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, Sequence
from uuid import NAMESPACE_URL, uuid5

from agent_platform.memory.boundaries import MemoryBoundaryPolicy, MemorySourceDomain
from agent_platform.memory.service import (
    MemoryCandidate,
    MemoryPrincipal,
    MemoryRecord,
    MemoryScope,
    MemoryService,
)

MAX_TURN_CHARS = 20_000
MAX_CANDIDATES_PER_TURN = 50
MAX_CANDIDATE_CONTENT_CHARS = 4_000


@dataclass(frozen=True)
class MemoryTurn:
    tenant_id: str
    user_id: str
    source_ref: str
    user_message: str
    assistant_message: str = ""
    agent_id: str | None = None
    project_id: str | None = None
    # True only after the final assistant message is durably appended to the session.
    completed: bool = False


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
        if not isinstance(turn.source_ref, str) or not turn.source_ref.strip():
            raise ValueError("source_ref is required for memory provenance")
        if not isinstance(turn.completed, bool) or not turn.completed:
            raise ValueError("memory extraction requires a completed assistant turn")
        reference = MemoryBoundaryPolicy.validate_reference(turn.source_ref)
        if reference.domain is not MemorySourceDomain.SESSION:
            raise ValueError("memory extraction source must be a session message")
        if reference.tenant_id != principal.tenant_id:
            raise PermissionError("session source reference belongs to another tenant")
        if not isinstance(turn.user_message, str) or not isinstance(turn.assistant_message, str):
            raise ValueError("interaction messages must be strings")
        if not turn.user_message.strip() or not turn.assistant_message.strip():
            raise ValueError("memory extraction requires both sides of a completed turn")
        if len(turn.user_message) + len(turn.assistant_message) > MAX_TURN_CHARS:
            raise ValueError("interaction turn exceeds the extraction size limit")
        if turn.agent_id and turn.agent_id not in principal.agent_ids:
            raise PermissionError("agent context is not authorized")
        if turn.project_id and turn.project_id not in principal.project_ids:
            raise PermissionError("project context is not authorized")

        # A completed assistant message is the idempotency key. Return already
        # proposed candidates before invoking a potentially expensive extractor.
        existing = await self.memory.repository.list(principal.tenant_id, limit=5000)
        prior = [
            record for record in existing
            if record.candidate.source_ref == turn.source_ref
            and record.candidate.metadata.get("extraction") == "single_turn"
            and record.status.value != "deleted"
        ]
        if prior:
            return prior

        drafts = await self.extractor.extract(turn=turn)
        if not isinstance(drafts, Sequence):
            raise ValueError("extractor must return a sequence of memory candidates")
        if len(drafts) > MAX_CANDIDATES_PER_TURN:
            raise ValueError("extractor returned too many memory candidates")

        # Preflight the complete batch before the first write. A malformed or
        # unauthorized later draft must not leave earlier candidates persisted.
        source_texts = {
            " ".join(text.casefold().split())
            for text in (turn.user_message, turn.assistant_message)
        }
        source_texts.add(
            " ".join((turn.user_message + " " + turn.assistant_message).casefold().split())
        )
        candidates: list[MemoryCandidate] = []
        for index, draft in enumerate(drafts):
            if not isinstance(draft, MemoryDraft):
                raise ValueError("extractor returned an invalid memory draft")
            if not isinstance(draft.content, str) or not draft.content.strip():
                raise ValueError("memory candidate content must be non-empty")
            if len(draft.content) > MAX_CANDIDATE_CONTENT_CHARS:
                raise ValueError("memory candidate content exceeds the size limit")
            normalized_content = " ".join(draft.content.casefold().split())
            if normalized_content in source_texts:
                raise ValueError(
                    "memory candidate must be distilled, not a verbatim interaction message"
                )
            if draft.scope is MemoryScope.USER:
                if draft.scope_id != principal.user_id:
                    raise PermissionError("user memory scope must match the principal")
            elif draft.scope is MemoryScope.AGENT:
                if (
                    draft.scope_id not in principal.manage_agent_ids
                    and "memory.manage" not in principal.permissions
                ):
                    raise PermissionError("agent memory management is not authorized")
            elif draft.scope is MemoryScope.PROJECT:
                if (
                    draft.scope_id not in principal.manage_project_ids
                    and "memory.manage" not in principal.permissions
                ):
                    raise PermissionError("project memory management is not authorized")
            elif draft.scope is MemoryScope.TENANT:
                if "memory.manage" not in principal.permissions:
                    raise PermissionError("tenant memory requires memory.manage")
            else:
                raise ValueError("memory draft has an unsupported scope")

            candidates.append(MemoryCandidate(
                tenant_id=principal.tenant_id,
                memory_id=str(uuid5(
                    NAMESPACE_URL,
                    principal.tenant_id + ":" + turn.source_ref + ":single-turn:" + str(index),
                )),
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
                    "extraction_index": str(index),
                    "agent_id": turn.agent_id or "",
                    "project_id": turn.project_id or "",
                },
            ))

        return [await self.memory.propose(candidate) for candidate in candidates]
