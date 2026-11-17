"""Governed graph extraction, candidate review and conflict resolution.

Extractors are replaceable (for example, an LLM-backed adapter); this module
owns provenance, validation, candidate lifecycle and idempotent graph writes.
No source-domain payload is copied into graph attributes.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol, Sequence

from agent_platform.graph.schema import (
    EntityTypeDefinition,
    GraphEntity,
    GraphRelation,
    GraphSchema,
    GraphSourceDomain,
    PropertyDefinition,
    PropertyKind,
    RelationTypeDefinition,
    SourceTypeMapping,
    default_graph_schema,
)

MAX_SOURCE_CHARS = 100_000
MAX_EXTRACTED_ENTITIES = 200
MAX_EXTRACTED_RELATIONS = 400
MAX_SPAN_CHARS = 1_000


class GraphSourceKind(str, Enum):
    KNOWLEDGE_DOCUMENT = "knowledge_document"
    MEMORY_RECORD = "memory_record"
    BLACKBOARD_EVENT = "blackboard_event"
    FILE_ASSET = "file_asset"


@dataclass(frozen=True)
class GraphSource:
    tenant_id: str
    source_kind: GraphSourceKind
    source_id: str
    source_version: int
    content: str
    project_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("tenant_id", "source_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{name} must contain 1-200 non-whitespace characters")
        if not isinstance(self.source_kind, GraphSourceKind):
            raise ValueError("source_kind must be a GraphSourceKind")
        if isinstance(self.source_version, bool) or not isinstance(self.source_version, int) or self.source_version < 1:
            raise ValueError("source_version must be a positive integer")
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValueError("source content must not be empty")
        if len(self.content) > MAX_SOURCE_CHARS:
            raise ValueError("source content exceeds the extraction size limit")
        if self.project_id is not None and (not isinstance(self.project_id, str) or not self.project_id.strip()):
            raise ValueError("project_id must be a non-empty string")


@dataclass(frozen=True)
class ExtractedEntity:
    name: str
    entity_type: str = "concept"
    attributes: dict[str, object] = field(default_factory=dict)
    confidence: float = 0.5
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class ExtractedRelation:
    source_name: str
    target_name: str
    relation_type: str = "related_to"
    attributes: dict[str, object] = field(default_factory=dict)
    confidence: float = 0.5
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True)
class ExtractionBatch:
    entities: Sequence[ExtractedEntity]
    relations: Sequence[ExtractedRelation]
    extractor_name: str
    extractor_version: str


class GraphExtractor(Protocol):
    async def extract(self, *, source: GraphSource) -> ExtractionBatch: ...


class RuleBasedGraphExtractor:
    """Small deterministic fallback extractor; production LLM extractors plug into GraphExtractor."""

    _quoted = re.compile(r"[“\"']([^”\"'\n]{2,80})[”\"']")
    _relation = re.compile(
        r"([\w\u4e00-\u9fff.-]{2,80})\s*(?:属于|隶属|位于|使用|关联|依赖|works for|located in|uses|depends on|related to)\s*([\w\u4e00-\u9fff.-]{2,80})",
        re.IGNORECASE,
    )

    async def extract(self, *, source: GraphSource) -> ExtractionBatch:
        entities: dict[str, ExtractedEntity] = {}
        for match in self._quoted.finditer(source.content):
            name = match.group(1).strip()
            entities.setdefault(name.casefold(), ExtractedEntity(
                name=name, confidence=0.72, start=match.start(1), end=match.end(1),
            ))
        relations: list[ExtractedRelation] = []
        for match in self._relation.finditer(source.content):
            left, right = match.group(1).strip(), match.group(2).strip()
            if left.casefold() == right.casefold():
                continue
            entities.setdefault(left.casefold(), ExtractedEntity(
                name=left, confidence=0.62, start=match.start(1), end=match.end(1),
            ))
            entities.setdefault(right.casefold(), ExtractedEntity(
                name=right, confidence=0.62, start=match.start(2), end=match.end(2),
            ))
            relations.append(ExtractedRelation(
                source_name=left, target_name=right, confidence=0.62,
                start=match.start(), end=match.end(),
            ))
        return ExtractionBatch(
            entities=tuple(entities.values()), relations=tuple(relations),
            extractor_name="rule-based", extractor_version="1",
        )


class CandidateStatus(str, Enum):
    PENDING = "pending"
    CONFLICT = "conflict"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class GraphCandidate:
    candidate_id: str
    tenant_id: str
    project_id: str | None
    source: GraphSource
    entities: tuple[ExtractedEntity, ...]
    relations: tuple[ExtractedRelation, ...]
    extractor_name: str
    extractor_version: str
    status: CandidateStatus = CandidateStatus.PENDING
    version: int = 1
    conflict_keys: tuple[str, ...] = ()
    reviewed_by: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class GraphReviewer:
    tenant_id: str
    user_id: str
    project_ids: frozenset[str] = frozenset()
    permissions: frozenset[str] = frozenset()


class GraphCandidateRepository(Protocol):
    async def get(self, tenant_id: str, candidate_id: str) -> GraphCandidate | None: ...
    async def find_source_revision(self, tenant_id: str, source_kind: str, source_id: str, source_version: int) -> GraphCandidate | None: ...
    async def find_conflicts(self, tenant_id: str, project_id: str | None, keys: Sequence[str]) -> Sequence[GraphCandidate]: ...
    async def put(self, candidate: GraphCandidate) -> None: ...


class InMemoryGraphCandidateRepository:
    """Reference adapter; production deployments should provide durable storage."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], GraphCandidate] = {}

    async def get(self, tenant_id: str, candidate_id: str) -> GraphCandidate | None:
        return self._items.get((tenant_id, candidate_id))

    async def find_source_revision(self, tenant_id: str, source_kind: str, source_id: str, source_version: int) -> GraphCandidate | None:
        return next((item for (tenant, _), item in self._items.items()
                     if tenant == tenant_id and item.source.source_kind.value == source_kind
                     and item.source.source_id == source_id and item.source.source_version == source_version), None)

    async def find_conflicts(self, tenant_id: str, project_id: str | None, keys: Sequence[str]) -> Sequence[GraphCandidate]:
        wanted = set(keys)
        return tuple(item for (tenant, _), item in self._items.items()
                     if tenant == tenant_id and item.project_id == project_id
                     and item.status in {CandidateStatus.PENDING, CandidateStatus.CONFLICT, CandidateStatus.APPROVED}
                     and wanted.intersection(item.conflict_keys))

    async def put(self, candidate: GraphCandidate) -> None:
        key = (candidate.tenant_id, candidate.candidate_id)
        current = self._items.get(key)
        if current is not None and candidate.version != current.version + 1:
            raise RuntimeError("graph candidate version conflict")
        if current is None and candidate.version != 1:
            raise RuntimeError("graph candidate version conflict")
        self._items[key] = candidate


class GraphWriteRepository(Protocol):
    async def upsert_entity(self, entity: GraphEntity, *, name: str, source_ref: object | None = None, allowed_project_ids: Sequence[str] = ()) -> object: ...
    async def upsert_relation(self, relation: GraphRelation, *, allowed_project_ids: Sequence[str] = ()) -> object: ...


def graph_pipeline_schema(tenant_id: str) -> GraphSchema:
    """Default schema with the generic extraction vocabulary and file provenance mapping."""
    base = default_graph_schema(tenant_id)
    entities = base.entity_types + (EntityTypeDefinition(
        "concept", (PropertyDefinition("description", PropertyKind.STRING, max_length=1000),),
        "A reviewable entity extracted from a canonical source.",
    ),)
    relations = base.relation_types + (RelationTypeDefinition(
        "related_to", ("concept",), ("concept",),
        description="A directed relation proposed by the extraction pipeline.",
    ),)
    mappings = base.source_mappings + (
        SourceTypeMapping(GraphSourceDomain.KNOWLEDGE, "file_asset", "document"),
    )
    return GraphSchema(tenant_id, entities, relations, mappings)


def _confidence(value: float) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
        raise ValueError("extraction confidence must be between 0 and 1")


def _entity_key(item: ExtractedEntity) -> str:
    return f"entity:{item.entity_type}:{' '.join(item.name.casefold().split())}"


def _candidate_id(source: GraphSource) -> str:
    raw = f"{source.tenant_id}:{source.source_kind.value}:{source.source_id}:{source.source_version}"
    return hashlib.sha256(raw.encode()).hexdigest()


class GraphBuildPipeline:
    def __init__(
        self, *, schema: GraphSchema, extractor: GraphExtractor,
        candidates: GraphCandidateRepository, graph: GraphWriteRepository,
    ) -> None:
        self.schema, self.extractor = schema, extractor
        self.candidates, self.graph = candidates, graph

    async def propose(self, *, source: GraphSource, allowed_project_ids: Sequence[str] = ()) -> GraphCandidate:
        if source.tenant_id != self.schema.tenant_id:
            raise PermissionError("source tenant does not match graph schema")
        if source.project_id is not None and source.project_id not in set(allowed_project_ids):
            raise PermissionError("source project is outside the authorized scope")
        existing = await self.candidates.find_source_revision(
            source.tenant_id, source.source_kind.value, source.source_id, source.source_version,
        )
        if existing is not None:
            return existing
        batch = await self.extractor.extract(source=source)
        if not isinstance(batch, ExtractionBatch):
            raise ValueError("extractor must return an ExtractionBatch")
        if len(batch.entities) > MAX_EXTRACTED_ENTITIES or len(batch.relations) > MAX_EXTRACTED_RELATIONS:
            raise ValueError("extractor output exceeds candidate limits")
        entities: dict[str, ExtractedEntity] = {}
        for item in batch.entities:
            if not isinstance(item, ExtractedEntity):
                raise ValueError("extractor returned an invalid entity")
            if not isinstance(item.name, str) or not item.name.strip() or len(item.name) > 200:
                raise ValueError("entity name must contain 1-200 characters")
            _confidence(item.confidence)
            self.schema.entity_type(item.entity_type)
            self.schema.validate_attributes(self.schema.entity_type(item.entity_type).properties, item.attributes)
            if item.start is not None and (item.start < 0 or item.end is None or item.end <= item.start or item.end > len(source.content)):
                raise ValueError("entity source span is invalid")
            entities.setdefault(_entity_key(item), item)
        relations: list[ExtractedRelation] = []
        for item in batch.relations:
            if not isinstance(item, ExtractedRelation):
                raise ValueError("extractor returned an invalid relation")
            _confidence(item.confidence)
            relation_type = self.schema.relation_type(item.relation_type)
            source_entity = entities.get(f"entity:concept:{' '.join(item.source_name.casefold().split())}")
            target_entity = entities.get(f"entity:concept:{' '.join(item.target_name.casefold().split())}")
            if source_entity is None or target_entity is None:
                raise ValueError("relation endpoints must be present in the extracted entity set")
            if "concept" not in relation_type.source_types or "concept" not in relation_type.target_types:
                raise ValueError("relation schema does not permit extracted concept endpoints")
            self.schema.validate_attributes(relation_type.properties, item.attributes)
            if item.start is not None and (item.start < 0 or item.end is None or item.end <= item.start or item.end > len(source.content)):
                raise ValueError("relation source span is invalid")
            relations.append(item)
        normalized = tuple(sorted(entities.values(), key=_entity_key))
        conflict_keys = tuple(sorted({_entity_key(item) for item in normalized}))
        conflicts = await self.candidates.find_conflicts(source.tenant_id, source.project_id, conflict_keys)
        conflict = any(
            _entity_key(old) == _entity_key(new)
            and dict(old.attributes) != dict(new.attributes)
            for old in (entity for record in conflicts for entity in record.entities)
            for new in normalized
        )
        now = datetime.now(timezone.utc)
        candidate = GraphCandidate(
            candidate_id=_candidate_id(source), tenant_id=source.tenant_id,
            project_id=source.project_id, source=source, entities=normalized,
            relations=tuple(relations), extractor_name=batch.extractor_name,
            extractor_version=batch.extractor_version,
            status=CandidateStatus.CONFLICT if conflict else CandidateStatus.PENDING,
            conflict_keys=conflict_keys, created_at=now, updated_at=now,
        )
        await self.candidates.put(candidate)
        return candidate

    async def review(self, *, principal: GraphReviewer, candidate_id: str, approve: bool) -> GraphCandidate:
        current = await self.candidates.get(principal.tenant_id, candidate_id)
        if current is None or current.status not in {CandidateStatus.PENDING, CandidateStatus.CONFLICT}:
            raise ValueError("graph candidate is not awaiting review")
        if (
            (current.project_id is None and "graph.manage" not in principal.permissions)
            or (current.project_id is not None and current.project_id not in principal.project_ids
                and "graph.manage" not in principal.permissions)
        ):
            raise PermissionError("graph candidate review is not authorized")
        if not isinstance(approve, bool):
            raise ValueError("approve must be a boolean")
        now = datetime.now(timezone.utc)
        if not approve:
            rejected = replace(current, status=CandidateStatus.REJECTED, version=current.version + 1,
                               reviewed_by=principal.user_id, updated_at=now)
            await self.candidates.put(rejected)
            return rejected
        if current.status is CandidateStatus.CONFLICT:
            raise ValueError("conflicted candidates require explicit resolve_conflict selection")
        await self._publish(current, principal)
        approved = replace(current, status=CandidateStatus.APPROVED, version=current.version + 1,
                           reviewed_by=principal.user_id, updated_at=now)
        await self.candidates.put(approved)
        return approved

    async def resolve_conflict(self, *, principal: GraphReviewer, conflict_candidate_id: str, selected_candidate_id: str) -> GraphCandidate:
        selected = await self.candidates.get(principal.tenant_id, selected_candidate_id)
        conflict = await self.candidates.get(principal.tenant_id, conflict_candidate_id)
        if selected is None or conflict is None or selected.status not in {CandidateStatus.CONFLICT, CandidateStatus.PENDING}:
            raise ValueError("selected candidate is not part of an open conflict")
        if not set(selected.conflict_keys).intersection(conflict.conflict_keys):
            raise ValueError("candidates do not share a conflict key")
        if (
            (selected.project_id is None and "graph.manage" not in principal.permissions)
            or (selected.project_id is not None and selected.project_id not in principal.project_ids
                and "graph.manage" not in principal.permissions)
        ):
            raise PermissionError("graph conflict resolution is not authorized")
        # Rejected alternatives remain in the candidate store for audit/provenance.
        now = datetime.now(timezone.utc)
        if selected.status is CandidateStatus.CONFLICT:
            selected = replace(selected, status=CandidateStatus.PENDING, version=selected.version + 1, updated_at=now)
            await self.candidates.put(selected)
        await self._publish(selected, principal)
        approved = replace(selected, status=CandidateStatus.APPROVED, version=selected.version + 1,
                           reviewed_by=principal.user_id, updated_at=now)
        await self.candidates.put(approved)
        if conflict.candidate_id != selected.candidate_id and conflict.status in {CandidateStatus.CONFLICT, CandidateStatus.PENDING}:
            await self.candidates.put(replace(conflict, status=CandidateStatus.REJECTED,
                                              version=conflict.version + 1, reviewed_by=principal.user_id,
                                              updated_at=now))
        return approved

    async def _publish(self, candidate: GraphCandidate, principal: GraphReviewer) -> None:
        project_ids = tuple(principal.project_ids)
        entity_ids: dict[str, str] = {}
        source_type = {
            GraphSourceKind.KNOWLEDGE_DOCUMENT: "document",
            GraphSourceKind.MEMORY_RECORD: "memory",
            GraphSourceKind.BLACKBOARD_EVENT: "entry",
            GraphSourceKind.FILE_ASSET: "file_asset",
        }[candidate.source.source_kind]
        source_domain = GraphSourceDomain.MEMORY if candidate.source.source_kind is GraphSourceKind.MEMORY_RECORD else (
            GraphSourceDomain.BLACKBOARD if candidate.source.source_kind is GraphSourceKind.BLACKBOARD_EVENT
            else GraphSourceDomain.KNOWLEDGE
        )
        for item in candidate.entities:
            entity_id = hashlib.sha256(
                f"{candidate.tenant_id}:{candidate.project_id or ''}:{item.entity_type}:{item.name.casefold()}".encode()
            ).hexdigest()
            entity_ids[item.name.casefold()] = entity_id
            entity = GraphEntity(
                tenant_id=candidate.tenant_id, entity_id=entity_id,
                entity_type=item.entity_type, attributes=dict(item.attributes),
                project_id=candidate.project_id,
            )
            # GraphSourceReference is accepted by the PostgreSQL adapter via its
            # structural fields; imports are delayed to keep storage optional.
            source_ref = None
            try:
                from agent_platform.graph.postgres_repository import GraphSourceReference
                source_ref = GraphSourceReference(
                    domain=source_domain, source_type=source_type,
                    source_id=candidate.source.source_id,
                    source_version=candidate.source.source_version,
                )
            except ImportError:
                pass
            await self.graph.upsert_entity(
                entity, name=item.name, source_ref=source_ref,
                allowed_project_ids=project_ids,
            )
        for item in candidate.relations:
            relation = GraphRelation(
                tenant_id=candidate.tenant_id,
                relation_id=hashlib.sha256(
                    f"{candidate.tenant_id}:{candidate.source.source_id}:{item.source_name}:{item.target_name}:{item.relation_type}".encode()
                ).hexdigest(),
                relation_type=item.relation_type,
                source_entity_id=entity_ids[item.source_name.casefold()],
                target_entity_id=entity_ids[item.target_name.casefold()],
                source_entity_type="concept", target_entity_type="concept",
                attributes=dict(item.attributes), project_id=candidate.project_id,
            )
            await self.graph.upsert_relation(relation, allowed_project_ids=project_ids)
