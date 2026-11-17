"""Policy-driven memory retrieval with pre-filtered access and source citations.

The retrieval layer intentionally uses deterministic lexical ranking. Deployments may
add an embedding/rerank adapter later without changing the authorization, temporal,
version, or citation contract defined here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import isfinite
from typing import Sequence

from agent_platform.memory.service import (
    MemoryPrincipal,
    MemoryRecord,
    MemoryScope,
    MemoryService,
    MemoryStatus,
)


@dataclass(frozen=True)
class MemoryRecallPolicy:
    """Bounds which memories are eligible for a retrieval request."""

    scopes: frozenset[MemoryScope] = frozenset()
    min_confidence: float = 0.0
    limit: int = 20
    max_age: timedelta | None = None
    as_of: datetime | None = None
    source_prefixes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.scopes, frozenset) or any(
            not isinstance(scope, MemoryScope) for scope in self.scopes
        ):
            raise ValueError("scopes must be a frozenset of MemoryScope values")
        if (
            isinstance(self.min_confidence, bool)
            or not isinstance(self.min_confidence, (int, float))
            or not isfinite(self.min_confidence)
            or not 0 <= self.min_confidence <= 1
        ):
            raise ValueError("min_confidence must be between 0 and 1")
        if isinstance(self.limit, bool) or not isinstance(self.limit, int) or not 1 <= self.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if self.max_age is not None and (
            not isinstance(self.max_age, timedelta) or self.max_age.total_seconds() <= 0
        ):
            raise ValueError("max_age must be a positive timedelta")
        if self.as_of is not None and (
            self.as_of.tzinfo is None or self.as_of.utcoffset() is None
        ):
            raise ValueError("as_of must be timezone-aware")
        if any(not isinstance(prefix, str) or not prefix.strip() for prefix in self.source_prefixes):
            raise ValueError("source_prefixes must contain non-empty strings")


@dataclass(frozen=True)
class MemoryCitation:
    """Traceable reference to the exact memory version used by an answer."""

    memory_id: str
    version: int
    source_ref: str
    scope: MemoryScope
    scope_id: str
    confidence: float
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class MemoryRecall:
    record: MemoryRecord
    citation: MemoryCitation
    score: float


def _tokens(value: str) -> frozenset[str]:
    return frozenset(token.casefold() for token in value.split() if token.strip())


def _metadata_time(record: MemoryRecord, key: str) -> datetime | None:
    raw = record.candidate.metadata.get(key)
    if raw is None:
        return None
    try:
        value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"memory metadata {key} must be an ISO-8601 timestamp") from exc
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"memory metadata {key} must include a timezone")
    return value


class MemoryRetrievalService:
    """Tenant-bound retrieval; authorization and lifecycle filters run before ranking."""

    def __init__(self, *, memory: MemoryService) -> None:
        self.memory = memory

    @staticmethod
    def _eligible(
        record: MemoryRecord,
        *,
        principal: MemoryPrincipal,
        policy: MemoryRecallPolicy,
        now: datetime,
    ) -> bool:
        candidate = record.candidate
        if candidate.tenant_id != principal.tenant_id:
            return False
        if record.status is not MemoryStatus.ACTIVE or record.deleted_at is not None:
            return False
        if not MemoryService._visible(record, principal):
            return False
        if policy.scopes and candidate.scope not in policy.scopes:
            return False
        if candidate.confidence < policy.min_confidence:
            return False
        if policy.source_prefixes and not candidate.source_ref.startswith(policy.source_prefixes):
            return False

        as_of = policy.as_of or now
        if record.created_at > as_of or record.updated_at > as_of:
            return False
        if candidate.expires_at is not None and candidate.expires_at <= as_of:
            return False

        valid_from = _metadata_time(record, "valid_from")
        valid_until = _metadata_time(record, "valid_until")
        if valid_from is not None and valid_from > as_of:
            return False
        if valid_until is not None and valid_until <= as_of:
            return False
        if policy.max_age is not None and record.updated_at < as_of - policy.max_age:
            return False
        return True

    @staticmethod
    def _score(record: MemoryRecord, query_tokens: frozenset[str], now: datetime) -> float:
        candidate_tokens = _tokens(record.candidate.content)
        lexical = (
            len(query_tokens & candidate_tokens) / len(query_tokens)
            if query_tokens else 0.0
        )
        age_days = max(0.0, (now - record.updated_at).total_seconds() / 86400)
        recency = 1.0 / (1.0 + age_days / 30.0)
        # Confidence is a calibrated prior, not a substitute for query relevance.
        return (lexical * 0.7) + (record.candidate.confidence * 0.2) + (recency * 0.1)

    async def retrieve(
        self,
        *,
        principal: MemoryPrincipal,
        query: str = "",
        policy: MemoryRecallPolicy | None = None,
    ) -> list[MemoryRecall]:
        if not isinstance(query, str):
            raise ValueError("query must be a string")
        recall_policy = policy or MemoryRecallPolicy()
        now = datetime.now(timezone.utc)
        query_tokens = _tokens(query)
        records: Sequence[MemoryRecord] = await self.memory.repository.list(
            principal.tenant_id, limit=5000
        )
        eligible = [
            record for record in records
            if self._eligible(
                record, principal=principal, policy=recall_policy, now=now
            )
        ]
        if query_tokens:
            eligible = [
                record for record in eligible
                if query_tokens & _tokens(record.candidate.content)
            ]
        ranked = sorted(
            eligible,
            key=lambda record: (
                self._score(record, query_tokens, now),
                record.updated_at,
                record.candidate.memory_id,
            ),
            reverse=True,
        )[:recall_policy.limit]
        return [
            MemoryRecall(
                record=record,
                citation=MemoryCitation(
                    memory_id=record.candidate.memory_id,
                    version=record.version,
                    source_ref=record.candidate.source_ref,
                    scope=record.candidate.scope,
                    scope_id=record.candidate.scope_id,
                    confidence=record.candidate.confidence,
                    created_at=record.created_at,
                    updated_at=record.updated_at,
                ),
                score=self._score(record, query_tokens, now),
            )
            for record in ranked
        ]

    async def version_history(
        self, *, principal: MemoryPrincipal, memory_id: str
    ) -> tuple[MemoryCitation, ...]:
        """Return version citations only after the same scope-management authorization."""
        current = await self.memory.repository.get(principal.tenant_id, memory_id)
        if current is None:
            raise ValueError("memory not found")
        if not MemoryService._can_manage_scope(
            current.candidate.scope, current.candidate.scope_id, principal
        ):
            raise PermissionError("memory version history is not authorized")
        list_versions = getattr(self.memory.repository, "list_versions", None)
        if list_versions is None:
            raise RuntimeError("memory repository does not support version history")
        versions = await list_versions(principal.tenant_id, memory_id)
        return tuple(
            MemoryCitation(
                memory_id=record.candidate.memory_id,
                version=record.version,
                source_ref=record.candidate.source_ref,
                scope=record.candidate.scope,
                scope_id=record.candidate.scope_id,
                confidence=record.candidate.confidence,
                created_at=record.created_at,
                updated_at=record.updated_at,
            )
            for record in versions
        )
