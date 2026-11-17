"""Policy-aware memory recall with temporal, scope, version and provenance controls."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from agent_platform.memory.service import (
    MemoryPrincipal,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemoryStatus,
)


class MemoryRecallStrategy(str, Enum):
    RELEVANCE = "relevance"
    CONFIDENCE = "confidence"
    RECENCY = "recency"


@dataclass(frozen=True)
class MemoryRecallQuery:
    text: str = ""
    scopes: frozenset[MemoryScope] = frozenset()
    as_of: datetime | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    min_confidence: float = 0.0
    source_ref_prefix: str | None = None
    strategy: MemoryRecallStrategy = MemoryRecallStrategy.RELEVANCE
    limit: int = 20


@dataclass(frozen=True)
class MemoryRecallHit:
    record: MemoryRecord
    score: float
    matched_terms: tuple[str, ...]


def _aware(value: datetime | None, name: str) -> None:
    if value is not None and (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{name} must be timezone-aware")


def _terms(value: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys(re.findall(r"[\w-]+", value.casefold())))


class MemoryRetrievalService:
    """Apply explicit temporal and recall policy on top of governed memory storage."""

    def __init__(self, memory: MemoryService) -> None:
        self.memory = memory

    @staticmethod
    def _validate(query: MemoryRecallQuery) -> None:
        if not isinstance(query, MemoryRecallQuery):
            raise ValueError("query must be a MemoryRecallQuery")
        _aware(query.as_of, "as_of")
        _aware(query.created_after, "created_after")
        _aware(query.created_before, "created_before")
        if (
            query.created_after is not None
            and query.created_before is not None
            and query.created_after > query.created_before
        ):
            raise ValueError("created_after must not be later than created_before")
        if not 0 <= query.min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if query.limit < 1 or query.limit > 100:
            raise ValueError("limit must be between 1 and 100")
        if not isinstance(query.strategy, MemoryRecallStrategy):
            raise ValueError("strategy must be a MemoryRecallStrategy")
        if any(not isinstance(scope, MemoryScope) for scope in query.scopes):
            raise ValueError("scopes must contain MemoryScope values")
        if query.source_ref_prefix is not None and not query.source_ref_prefix.strip():
            raise ValueError("source_ref_prefix must be non-empty")

    @staticmethod
    def _applicable(record: MemoryRecord, at: datetime) -> bool:
        candidate = record.candidate
        if candidate.expires_at is not None and candidate.expires_at <= at:
            return False
        if candidate.applies_from is not None and candidate.applies_from > at:
            return False
        if candidate.applies_until is not None and candidate.applies_until <= at:
            return False
        return True

    @staticmethod
    def _score(
        record: MemoryRecord,
        *,
        terms: tuple[str, ...],
        strategy: MemoryRecallStrategy,
        now: datetime,
    ) -> tuple[float, tuple[str, ...]]:
        content = record.candidate.content.casefold()
        matched = tuple(term for term in terms if term in content)
        relevance = len(matched) / max(len(terms), 1) if terms else 0.0
        confidence = float(record.candidate.confidence)
        age_days = max((now - record.updated_at).total_seconds(), 0.0) / 86400
        recency = 1.0 / (1.0 + age_days)
        if strategy is MemoryRecallStrategy.CONFIDENCE:
            score = confidence
        elif strategy is MemoryRecallStrategy.RECENCY:
            score = recency
        else:
            score = (0.65 * relevance + 0.25 * confidence + 0.10 * recency) if terms else (0.7 * confidence + 0.3 * recency)
        return score, matched

    async def retrieve(
        self, *, principal: MemoryPrincipal, query: MemoryRecallQuery | None = None
    ) -> list[MemoryRecallHit]:
        policy = query or MemoryRecallQuery()
        self._validate(policy)
        at = policy.as_of or datetime.now(timezone.utc)

        records = await self.memory.retrieve(principal=principal, limit=1000)

        hits: list[MemoryRecallHit] = []
        terms = _terms(policy.text)
        for record in records:
            candidate = record.candidate
            if policy.scopes and candidate.scope not in policy.scopes:
                continue
            if not self._applicable(record, at):
                continue
            if policy.created_after and record.created_at < policy.created_after:
                continue
            if policy.created_before and record.created_at > policy.created_before:
                continue
            if candidate.confidence < policy.min_confidence:
                continue
            if policy.source_ref_prefix and not candidate.source_ref.startswith(policy.source_ref_prefix):
                continue
            score, matched = self._score(
                record, terms=terms, strategy=policy.strategy, now=at,
            )
            if terms and policy.strategy is MemoryRecallStrategy.RELEVANCE and not matched:
                continue
            hits.append(MemoryRecallHit(record=record, score=score, matched_terms=matched))
        hits.sort(
            key=lambda hit: (
                hit.score,
                hit.record.updated_at,
                hit.record.candidate.memory_id,
            ),
            reverse=True,
        )
        return hits[:policy.limit]

    async def retrieve_version(
        self,
        *,
        principal: MemoryPrincipal,
        memory_id: str,
        version: int,
        as_of: datetime | None = None,
    ) -> MemoryRecallHit:
        if version < 1:
            raise ValueError("version must be a positive integer")
        _aware(as_of, "as_of")
        current = await self.memory.repository.get(principal.tenant_id, memory_id)
        if current is None:
            raise ValueError("memory not found")
        if not self.memory._can_manage_scope(
            current.candidate.scope, current.candidate.scope_id, principal
        ) and not self.memory._visible(current, principal):
            raise PermissionError("memory version access is not authorized")
        history = await self.memory.history(principal=principal, memory_id=memory_id)
        record = next((item for item in history if item.version == version), None)
        if record is None:
            raise ValueError("memory version not found")
        at = as_of or datetime.now(timezone.utc)
        if record.status is not MemoryStatus.ACTIVE or not self._applicable(record, at):
            raise ValueError("memory version is not applicable for retrieval")
        score, matched = self._score(
            record, terms=(), strategy=MemoryRecallStrategy.CONFIDENCE, now=at,
        )
        return MemoryRecallHit(record=record, score=score, matched_terms=matched)
