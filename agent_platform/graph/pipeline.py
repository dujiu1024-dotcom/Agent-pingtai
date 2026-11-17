"""Candidate-first graph extraction from canonical platform records."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Protocol, Sequence

from agent_platform.graph.schema import GraphEntity, GraphRelation, GraphSchema, GraphSourceDomain


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class GraphPrincipal:
    tenant_id: str
    user_id: str
    project_ids: frozenset[str] = frozenset()
    manage_project_ids: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()


@dataclass(frozen=True)
class GraphSourceRecord:
    """Canonical source resolved and ACL-checked by the host."""
    tenant_id: str
    domain: GraphSourceDomain
    source_type: str
    source_id: str
    source_version: int
    content: str
    project_id: str | None = None
    display_name: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("source tenant_id is required")
        if not isinstance(self.domain, GraphSourceDomain):
            raise ValueError("source domain must be a GraphSourceDomain")
        for name in ("source_type", "source_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{name} must contain 1-200 characters")
        if isinstance(self.source_version, bool) or not isinstance(self.source_version, int) or self.source_version < 1:
            raise ValueError("source_version must be a positive integer")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("source content must be a non-empty string")
        if len(self.content) > 200_000:
            raise ValueError("source content exceeds the extraction limit")
        if self.project_id is not None and (
            not isinstance(self.project_id, str) or not self.project_id.strip()
        ):
            raise ValueError("project_id must be a non-empty string")
        if not isinstance(self.display_name, str) or len(self.display_name) > 500:
            raise ValueError("display_name must be a string of at most 500 characters")


@dataclass(frozen=True)
class GraphSourceReference:
    domain: GraphSourceDomain
    source_type: str
    source_id: str
    source_version: int

    def __post_init__(self) -> None:
        if not isinstance(self.domain, GraphSourceDomain):
            raise ValueError("source reference domain is invalid")
        if any(not isinstance(v, str) or not v.strip() or len(v) > 200
               for v in (self.source_type, self.source_id)):
            raise ValueError("source reference identifiers must be non-empty and bounded")
        if isinstance(self.source_version, bool) or not isinstance(self.source_version, int) or self.source_version < 1:
            raise ValueError("source reference version must be a positive integer")


@dataclass(frozen=True)
class GraphExtractionDraft:
    entity: GraphEntity
    name: str
    confidence: float
    relations: tuple[GraphRelation, ...] = ()
    conflict_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.entity, GraphEntity):
            raise ValueError("extraction draft entity is invalid")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 500:
            raise ValueError("extracted entity name must contain 1-500 characters")
        if (isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float))
                or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1):
            raise ValueError("confidence must be a finite number between 0 and 1")
        if not isinstance(self.relations, tuple) or any(not isinstance(r, GraphRelation) for r in self.relations):
            raise ValueError("relations must be a tuple of GraphRelation")
        if self.conflict_key is not None and (
            not isinstance(self.conflict_key, str) or not self.conflict_key.strip()
            or len(self.conflict_key) > 200
        ):
            raise ValueError("conflict_key must be a non-empty string of at most 200 characters")


class GraphCandidateStatus(str, Enum):
    PENDING = "pending"
    REVIEW_REQUIRED = "review_required"
    CONFLICT = "conflict"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True)
class GraphExtractionCandidate:
    candidate_id: str
    tenant_id: str
    source_reference: GraphSourceReference
    entity: GraphEntity
    name: str
    confidence: float
    relations: tuple[GraphRelation, ...]
    conflict_key: str | None
    status: GraphCandidateStatus = GraphCandidateStatus.PENDING
    version: int = 1
    created_at: datetime = field(default_factory=_now)
    reviewed_by: str | None = None
    updated_at: datetime = field(default_factory=_now)

    def __post_init__(self) -> None:
        if not isinstance(self.status, GraphCandidateStatus):
            raise ValueError("candidate status is invalid")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("candidate version must be a positive integer")
        if self.entity.tenant_id != self.tenant_id:
            raise ValueError("candidate and entity tenant must match")
        for value in (self.created_at, self.updated_at):
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("candidate timestamps must be timezone-aware")


class GraphExtractor(Protocol):
    async def extract(self, *, source: GraphSourceRecord, schema: GraphSchema) -> Sequence[GraphExtractionDraft]: ...


class GraphCandidateRepository(Protocol):
    async def get(self, tenant_id: str, candidate_id: str) -> GraphExtractionCandidate | None: ...
    async def list(self, tenant_id: str, *, limit: int = 1000) -> Sequence[GraphExtractionCandidate]: ...
    async def put(self, candidate: GraphExtractionCandidate) -> None: ...


class GraphRepository(Protocol):
    async def upsert_entity(
        self, entity: GraphEntity, *, name: str,
        source_ref: GraphSourceReference | None = None,
        allowed_project_ids: Sequence[str] = (),
    ) -> Any: ...
    async def upsert_relation(self, relation: GraphRelation, *, allowed_project_ids: Sequence[str] = ()) -> Any: ...
    async def delete_entity(self, entity_id: str, *, allowed_project_ids: Sequence[str] = ()) -> bool: ...


class InMemoryGraphCandidateRepository:
    """Test/development adapter; production must inject a durable tenant store."""
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], GraphExtractionCandidate] = {}

    async def get(self, tenant_id: str, candidate_id: str) -> GraphExtractionCandidate | None:
        return self._items.get((tenant_id, candidate_id))

    async def list(self, tenant_id: str, *, limit: int = 1000) -> Sequence[GraphExtractionCandidate]:
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        return tuple(item for (tenant, _), item in self._items.items() if tenant == tenant_id)[:limit]

    async def put(self, candidate: GraphExtractionCandidate) -> None:
        key = (candidate.tenant_id, candidate.candidate_id)
        current = self._items.get(key)
        expected = 1 if current is None else current.version + 1
        if candidate.version != expected:
            raise RuntimeError("graph candidate version conflict")
        self._items[key] = candidate


class GraphExtractionPipeline:
    """Extract, review and persist graph projections with explicit source lineage."""
    def __init__(
        self, *, schema: GraphSchema, extractor: GraphExtractor,
        candidates: GraphCandidateRepository, graph: GraphRepository,
        max_candidates: int = 100, minimum_confidence: float = 0.55,
    ) -> None:
        if isinstance(max_candidates, bool) or not isinstance(max_candidates, int) or not 1 <= max_candidates <= 500:
            raise ValueError("max_candidates must be between 1 and 500")
        if (isinstance(minimum_confidence, bool) or not isinstance(minimum_confidence, (int, float))
                or not math.isfinite(minimum_confidence) or not 0 <= minimum_confidence <= 1):
            raise ValueError("minimum_confidence must be between 0 and 1")
        self.schema, self.extractor = schema, extractor
        self.candidates, self.graph = candidates, graph
        self.max_candidates, self.minimum_confidence = max_candidates, float(minimum_confidence)

    async def propose(self, *, source: GraphSourceRecord, principal: GraphPrincipal) -> list[GraphExtractionCandidate]:
        self._authorize_source(source, principal)
        mapping = next((item for item in self.schema.source_mappings
                        if item.domain is source.domain and item.source_type == source.source_type), None)
        if mapping is None:
            raise ValueError("canonical source type is not mapped by the graph schema")
        drafts = await self.extractor.extract(source=source, schema=self.schema)
        if not isinstance(drafts, Sequence) or isinstance(drafts, (str, bytes)):
            raise ValueError("extractor must return a sequence of graph drafts")
        if len(drafts) > self.max_candidates:
            raise ValueError("extractor returned too many graph candidates")
        source_ref = GraphSourceReference(source.domain, source.source_type, source.source_id, source.source_version)
        existing = list(await self.candidates.list(source.tenant_id, limit=5000))
        proposed: list[GraphExtractionCandidate] = []
        for draft in drafts:
            if not isinstance(draft, GraphExtractionDraft):
                raise ValueError("extractor returned an invalid graph draft")
            if draft.entity.tenant_id != source.tenant_id:
                raise ValueError("extracted entity tenant does not match source tenant")
            if draft.entity.project_id != source.project_id:
                raise ValueError("extracted entity project must match its canonical source")
            if draft.entity.entity_type != mapping.entity_type:
                raise ValueError("extracted entity type must match the canonical source mapping")
            draft.entity.validate(self.schema)
            for relation in draft.relations:
                if relation.tenant_id != source.tenant_id or relation.project_id != source.project_id:
                    raise ValueError("extracted relation tenant/project must match its canonical source")
                relation.validate(self.schema)
            candidate_id = self._candidate_id(source_ref, draft.entity.entity_id)
            current = await self.candidates.get(source.tenant_id, candidate_id)
            if current is not None:
                if (current.entity.attributes != draft.entity.attributes or current.name != draft.name.strip()
                        or current.relations != draft.relations):
                    raise ValueError("extractor output changed for an existing source revision")
                proposed.append(current)
                continue
            conflict = bool(draft.conflict_key) and any(
                item.conflict_key == draft.conflict_key
                and item.entity.entity_type == draft.entity.entity_type
                and item.entity.project_id == draft.entity.project_id
                and item.entity.attributes != draft.entity.attributes
                and item.status in {GraphCandidateStatus.PENDING, GraphCandidateStatus.REVIEW_REQUIRED,
                                    GraphCandidateStatus.CONFLICT, GraphCandidateStatus.CONFIRMED}
                for item in existing
            )
            status = (GraphCandidateStatus.CONFLICT if conflict else
                      GraphCandidateStatus.REVIEW_REQUIRED if draft.confidence < self.minimum_confidence
                      else GraphCandidateStatus.PENDING)
            candidate = GraphExtractionCandidate(
                candidate_id=candidate_id, tenant_id=source.tenant_id, source_reference=source_ref,
                entity=draft.entity, name=draft.name.strip(), confidence=float(draft.confidence),
                relations=draft.relations, conflict_key=draft.conflict_key, status=status,
            )
            await self.candidates.put(candidate)
            existing.append(candidate)
            proposed.append(candidate)
        return proposed

    async def confirm(self, *, principal: GraphPrincipal, candidate_id: str) -> GraphExtractionCandidate:
        candidate = await self.candidates.get(principal.tenant_id, candidate_id)
        if candidate is None:
            raise ValueError("graph candidate not found")
        if candidate.status not in {GraphCandidateStatus.PENDING, GraphCandidateStatus.REVIEW_REQUIRED}:
            raise ValueError("only reviewable graph candidates can be confirmed")
        self._authorize_review(candidate, principal)
        await self._persist(candidate, principal)
        updated = replace(candidate, status=GraphCandidateStatus.CONFIRMED,
                          reviewed_by=principal.user_id, version=candidate.version + 1, updated_at=_now())
        await self.candidates.put(updated)
        return updated

    async def resolve_conflict(self, *, principal: GraphPrincipal, candidate_id: str) -> GraphExtractionCandidate:
        selected = await self.candidates.get(principal.tenant_id, candidate_id)
        if selected is None or selected.status is not GraphCandidateStatus.CONFLICT or not selected.conflict_key:
            raise ValueError("conflict candidate not found")
        self._authorize_review(selected, principal)
        group = [item for item in await self.candidates.list(principal.tenant_id, limit=5000)
                 if item.conflict_key == selected.conflict_key
                 and item.entity.entity_type == selected.entity.entity_type
                 and item.entity.project_id == selected.entity.project_id
                 and item.status in {GraphCandidateStatus.PENDING, GraphCandidateStatus.REVIEW_REQUIRED,
                                     GraphCandidateStatus.CONFLICT, GraphCandidateStatus.CONFIRMED}]
        allowed = tuple(sorted(principal.project_ids))
        for sibling in group:
            if sibling.candidate_id != selected.candidate_id and sibling.status is GraphCandidateStatus.CONFIRMED:
                await self.graph.delete_entity(sibling.entity.entity_id, allowed_project_ids=allowed)
        await self._persist(selected, principal)
        updated = replace(selected, status=GraphCandidateStatus.CONFIRMED,
                          reviewed_by=principal.user_id, version=selected.version + 1, updated_at=_now())
        await self.candidates.put(updated)
        for sibling in group:
            if sibling.candidate_id != selected.candidate_id:
                await self.candidates.put(replace(
                    sibling, status=GraphCandidateStatus.SUPERSEDED, reviewed_by=principal.user_id,
                    version=sibling.version + 1, updated_at=_now(),
                ))
        return updated

    async def reject(self, *, principal: GraphPrincipal, candidate_id: str, reason: str) -> bool:
        candidate = await self.candidates.get(principal.tenant_id, candidate_id)
        if candidate is None:
            return False
        self._authorize_review(candidate, principal)
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError("rejection reason must contain 1-1000 characters")
        if candidate.status in {GraphCandidateStatus.CONFIRMED, GraphCandidateStatus.REJECTED,
                                GraphCandidateStatus.SUPERSEDED}:
            return False
        await self.candidates.put(replace(candidate, status=GraphCandidateStatus.REJECTED,
                                          reviewed_by=principal.user_id, version=candidate.version + 1,
                                          updated_at=_now()))
        return True

    async def _persist(self, candidate: GraphExtractionCandidate, principal: GraphPrincipal) -> None:
        allowed = tuple(sorted(principal.project_ids))
        await self.graph.upsert_entity(candidate.entity, name=candidate.name,
                                       source_ref=candidate.source_reference,
                                       allowed_project_ids=allowed)
        for relation in candidate.relations:
            await self.graph.upsert_relation(relation, allowed_project_ids=allowed)

    def _authorize_source(self, source: GraphSourceRecord, principal: GraphPrincipal) -> None:
        if source.tenant_id != principal.tenant_id or source.tenant_id != self.schema.tenant_id:
            raise PermissionError("graph source tenant does not match authenticated principal")
        if source.project_id is not None and source.project_id not in principal.project_ids:
            raise PermissionError("graph source project access denied")
        if source.project_id is None and "graph.manage" not in principal.permissions:
            raise PermissionError("tenant-wide graph extraction requires graph.manage")
        if not ({"graph.extract", "graph.manage"} & set(principal.permissions)):
            raise PermissionError("graph extraction permission required")

    @staticmethod
    def _authorize_review(candidate: GraphExtractionCandidate, principal: GraphPrincipal) -> None:
        if candidate.tenant_id != principal.tenant_id:
            raise PermissionError("graph candidate tenant mismatch")
        project_id = candidate.entity.project_id
        allowed = ("graph.manage" in principal.permissions if project_id is None else
                   project_id in principal.manage_project_ids or "graph.manage" in principal.permissions)
        if not allowed:
            raise PermissionError("graph candidate review is not authorized")

    @staticmethod
    def _candidate_id(source: GraphSourceReference, entity_id: str) -> str:
        raw = json.dumps([source.domain.value, source.source_type, source.source_id,
                          source.source_version, entity_id], ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()


__all__ = [
    "GraphCandidateRepository", "GraphCandidateStatus", "GraphExtractionCandidate",
    "GraphExtractionDraft", "GraphExtractionPipeline", "GraphExtractor", "GraphPrincipal",
    "GraphRepository", "GraphSourceRecord", "GraphSourceReference", "InMemoryGraphCandidateRepository",
]
