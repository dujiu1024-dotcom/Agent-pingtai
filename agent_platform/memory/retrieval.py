"""Version-aware, permission-filtered memory retrieval with source citations."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from math import isfinite
from typing import Sequence

from agent_platform.memory.service import (
    MemoryPrincipal,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemoryStatus,
)


class MemoryRecallStrategy(str, Enum):
    RECENCY = "recency"
    CONFIDENCE = "confidence"
    RELEVANCE = "relevance"


@dataclass(frozen=True)
class MemoryRetrievalQuery:
    """A bounded retrieval request. All timestamps are UTC-offset aware."""

    scopes: frozenset[MemoryScope] = frozenset()
    as_of: datetime | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    source_refs: frozenset[str] = frozenset()
    text: str | None = None
    min_confidence: float = 0.0
    strategy: MemoryRecallStrategy = MemoryRecallStrategy.RELEVANCE
    limit: int = 20

    def __post_init__(self) -> None:
        for name in ("as_of", "created_after", "created_before"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, datetime)
                or value.tzinfo is None
                or value.utcoffset() is None
            ):
                raise ValueError(f"{name} must be timezone-aware")
        if (
            self.created_after is not None
            and self.created_before is not None
            and self.created_after > self.created_before
        ):
            raise ValueError("created_after must not be later than created_before")
        if not isinstance(self.scopes, frozenset) or any(
            not isinstance(scope, MemoryScope) for scope in self.scopes
        ):
            raise ValueError("scopes must be a frozenset of MemoryScope values")
        if not isinstance(self.source_refs, frozenset) or any(
            not isinstance(ref, str) or not ref.strip() for ref in self.source_refs
        ):
            raise ValueError("source_refs must be a frozenset of non-empty strings")
        if (
            isinstance(self.min_confidence, bool)
            or not isinstance(self.min_confidence, (int, float))
            or not isfinite(self.min_confidence)
            or not 0 <= self.min_confidence <= 1
        ):
            raise ValueError("min_confidence must be a finite number between 0 and 1")
        if not isinstance(self.strategy, MemoryRecallStrategy):
            raise ValueError("strategy must be a MemoryRecallStrategy")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError("limit must be an integer between 1 and 100")
        if self.text is not None and (
            not isinstance(self.text, str) or not self.text.strip()
        ):
            raise ValueError("text must be a non-empty string when provided")


@dataclass(frozen=True)
class MemoryCitation:
    memory_id: str
    version: int
    source_ref: str
    scope: MemoryScope
    scope_id: str
    recorded_at: datetime


@dataclass(frozen=True)
class MemoryRetrievalHit:
    record: MemoryRecord
    citation: MemoryCitation
    score: float


class MemoryRetrievalService:
    """Returns the authorized memory version that was valid at the requested time."""

    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory

    @staticmethod
    def _visible(record: MemoryRecord, principal: MemoryPrincipal, at: datetime) -> bool:
        candidate = record.candidate
        if candidate.tenant_id != principal.tenant_id or record.status is not MemoryStatus.ACTIVE:
            return False
        if candidate.expires_at is not None and candidate.expires_at <= at:
            return False
        if candidate.scope is MemoryScope.USER:
            return candidate.scope_id == principal.user_id or "memory.manage" in principal.permissions
        if candidate.scope is MemoryScope.AGENT:
            return candidate.scope_id in principal.agent_ids or "memory.manage" in principal.permissions
        if candidate.scope is MemoryScope.PROJECT:
            return candidate.scope_id in principal.project_ids or "memory.manage" in principal.permissions
        return candidate.scope_id == principal.tenant_id and "memory.manage" in principal.permissions

    @staticmethod
    def _text_score(content: str, text: str | None) -> float:
        if text is None:
            return 0.0
        terms = set(re.findall(r"[\w-]+", text.casefold()))
        words = set(re.findall(r"[\w-]+", content.casefold()))
        return len(terms & words) / len(terms) if terms else 0.0

    async def search(
        self,
        *,
        principal: MemoryPrincipal,
        query: MemoryRetrievalQuery | None = None,
    ) -> list[MemoryRetrievalHit]:
        request = query or MemoryRetrievalQuery()
        at = request.as_of or datetime.now(timezone.utc)
        list_versions = getattr(self.memory.repository, "list_versions", None)
        if list_versions is None:
            raise RuntimeError("memory repository does not support version-aware retrieval")

        current_records = await self.memory.repository.list(principal.tenant_id, limit=5000)
        hits: list[MemoryRetrievalHit] = []
        for current in current_records:
            versions: Sequence[MemoryRecord] = await list_versions(
                principal.tenant_id, current.candidate.memory_id
            )
            # Version snapshots carry their own recorded timestamp. Select the
            # latest snapshot at/before as_of, then evaluate its status and ACL;
            # this preserves historical reads without reviving deleted data now.
            eligible = [item for item in versions if item.created_at <= at]
            if not eligible:
                continue
            record = max(eligible, key=lambda item: (item.created_at, item.version))
            if not self._visible(record, principal, at):
                continue
            candidate = record.candidate
            if request.scopes and candidate.scope not in request.scopes:
                continue
            if request.source_refs and candidate.source_ref not in request.source_refs:
                continue
            if request.created_after and record.created_at < request.created_after:
                continue
            if request.created_before and record.created_at > request.created_before:
                continue
            if candidate.confidence < request.min_confidence:
                continue

            relevance = self._text_score(candidate.content, request.text)
            if request.strategy is MemoryRecallStrategy.RELEVANCE and request.text and relevance <= 0:
                continue
            if request.strategy is MemoryRecallStrategy.RECENCY:
                score = record.created_at.timestamp()
            elif request.strategy is MemoryRecallStrategy.CONFIDENCE:
                score = candidate.confidence
            else:
                recency = min(max(record.created_at.timestamp(), 0), at.timestamp()) / max(at.timestamp(), 1)
                score = relevance * 0.7 + candidate.confidence * 0.2 + recency * 0.1

            citation = MemoryCitation(
                memory_id=candidate.memory_id,
                version=record.version,
                source_ref=candidate.source_ref,
                scope=candidate.scope,
                scope_id=candidate.scope_id,
                recorded_at=record.updated_at,
            )
            hits.append(MemoryRetrievalHit(record=record, citation=citation, score=score))

        if request.strategy is MemoryRecallStrategy.RECENCY:
            hits.sort(key=lambda hit: (hit.record.created_at, hit.record.version), reverse=True)
        elif request.strategy is MemoryRecallStrategy.CONFIDENCE:
            hits.sort(
                key=lambda hit: (hit.record.candidate.confidence, hit.record.created_at),
                reverse=True,
            )
        else:
            hits.sort(key=lambda hit: (hit.score, hit.record.created_at), reverse=True)
        return hits[:request.limit]
