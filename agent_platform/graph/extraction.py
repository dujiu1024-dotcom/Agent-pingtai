"""Candidate-first graph extraction with source provenance and review gates."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Protocol, Sequence

from agent_platform.graph.schema import GraphSchema, GraphSourceDomain

MAX_SOURCE_CHARS = 100_000
MAX_DRAFTS_PER_SOURCE = 200
MAX_EVIDENCE_REFS = 32
MAX_METADATA_CHARS = 8_000


@dataclass(frozen=True)
class GraphSourceRecord:
    tenant_id: str
    domain: GraphSourceDomain
    source_type: str
    source_id: str
    source_version: str
    source_ref: str
    content: str
    project_id: str | None = None


@dataclass(frozen=True)
class GraphEntityDraft:
    local_id: str
    entity_type: str
    attributes: dict[str, Any]
    confidence: float
    evidence_refs: tuple[str, ...]
    conflict_key: str | None = None


@dataclass(frozen=True)
class GraphRelationDraft:
    local_id: str
    relation_type: str
    source_local_id: str
    target_local_id: str
    attributes: dict[str, Any]
    confidence: float
    evidence_refs: tuple[str, ...]
    conflict_key: str | None = None


@dataclass(frozen=True)
class GraphExtractionDraft:
    entities: tuple[GraphEntityDraft, ...]
    relations: tuple[GraphRelationDraft, ...]


@dataclass(frozen=True)
class GraphExtractionCandidate:
    candidate_id: str
    tenant_id: str
    domain: GraphSourceDomain
    source_type: str
    source_id: str
    source_version: str
    source_ref: str
    project_id: str | None
    candidate_kind: str
    local_id: str
    payload: dict[str, Any]
    confidence: float
    evidence_refs: tuple[str, ...]
    conflict_key: str | None
    status: str = "candidate"


class GraphExtractor(Protocol):
    async def extract(
        self, *, source: GraphSourceRecord, schema: GraphSchema
    ) -> GraphExtractionDraft: ...


class GraphCandidateStore(Protocol):
    async def append_candidates(
        self, candidates: Sequence[GraphExtractionCandidate]
    ) -> int: ...


class GraphExtractionService:
    """Validate and persist extraction output as reviewable candidates only.

    The caller must authorize access to the canonical source before invoking
    this service. Extractors receive one bounded source record, never a corpus.
    """

    def __init__(
        self, *, schema_provider: Callable[[str], GraphSchema],
        extractor: GraphExtractor, candidate_store: GraphCandidateStore,
    ) -> None:
        self.schema_provider = schema_provider
        self.extractor = extractor
        self.candidate_store = candidate_store

    async def extract_source(self, *, source: GraphSourceRecord) -> tuple[GraphExtractionCandidate, ...]:
        self._validate_source(source)
        schema = self.schema_provider(source.tenant_id)
        if schema.tenant_id != source.tenant_id:
            raise ValueError("graph schema tenant does not match source tenant")
        mappings = {
            (mapping.domain, mapping.source_type): mapping.entity_type
            for mapping in schema.source_mappings
        }
        if (source.domain, source.source_type) not in mappings:
            raise ValueError("source domain/type has no graph schema mapping")

        draft = await self.extractor.extract(source=source, schema=schema)
        if not isinstance(draft, GraphExtractionDraft):
            raise ValueError("extractor must return GraphExtractionDraft")
        if len(draft.entities) + len(draft.relations) > MAX_DRAFTS_PER_SOURCE:
            raise ValueError("extractor returned too many graph candidates")

        entities: dict[str, GraphEntityDraft] = {}
        candidates: list[GraphExtractionCandidate] = []
        for entity in draft.entities:
            if not isinstance(entity, GraphEntityDraft):
                raise ValueError("extractor returned an invalid entity draft")
            self._validate_local_id(entity.local_id)
            if entity.local_id in entities:
                raise ValueError("extractor returned duplicate local entity IDs")
            schema.validate_attributes(
                schema.entity_type(entity.entity_type).properties, entity.attributes
            )
            self._validate_confidence(entity.confidence)
            evidence = self._validate_evidence(entity.evidence_refs, source.source_ref)
            entities[entity.local_id] = entity
            candidates.append(self._candidate(
                source=source, kind="entity", local_id=entity.local_id,
                payload={"entity_type": entity.entity_type, "attributes": entity.attributes},
                confidence=entity.confidence, evidence=evidence,
                conflict_key=entity.conflict_key,
            ))

        relation_ids: set[str] = set()
        for relation in draft.relations:
            if not isinstance(relation, GraphRelationDraft):
                raise ValueError("extractor returned an invalid relation draft")
            self._validate_local_id(relation.local_id)
            if relation.local_id in relation_ids:
                raise ValueError("extractor returned duplicate local relation IDs")
            relation_ids.add(relation.local_id)
            if relation.source_local_id not in entities or relation.target_local_id not in entities:
                raise ValueError("relation references an unknown local entity")
            definition = schema.relation_type(relation.relation_type)
            source_entity = entities[relation.source_local_id]
            target_entity = entities[relation.target_local_id]
            if source_entity.entity_type not in definition.source_types:
                raise ValueError("relation source entity type is not allowed")
            if target_entity.entity_type not in definition.target_types:
                raise ValueError("relation target entity type is not allowed")
            schema.validate_attributes(definition.properties, relation.attributes)
            self._validate_confidence(relation.confidence)
            evidence = self._validate_evidence(relation.evidence_refs, source.source_ref)
            candidates.append(self._candidate(
                source=source, kind="relation", local_id=relation.local_id,
                payload={
                    "relation_type": relation.relation_type,
                    "source_local_id": relation.source_local_id,
                    "target_local_id": relation.target_local_id,
                    "attributes": relation.attributes,
                },
                confidence=relation.confidence, evidence=evidence,
                conflict_key=relation.conflict_key,
            ))

        candidates = self._mark_batch_conflicts(candidates)
        # One append call ensures the store can persist the complete source batch
        # atomically; invalid later drafts never leave partial candidate rows.
        await self.candidate_store.append_candidates(candidates)
        return tuple(candidates)

    @staticmethod
    def _validate_source(source: GraphSourceRecord) -> None:
        if not isinstance(source, GraphSourceRecord):
            raise ValueError("source must be a GraphSourceRecord")
        if not isinstance(source.tenant_id, str) or not source.tenant_id.strip():
            raise ValueError("source tenant_id is required")
        if not isinstance(source.domain, GraphSourceDomain):
            raise ValueError("source domain is invalid")
        for name in ("source_type", "source_id", "source_version", "source_ref"):
            value = getattr(source, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 512:
                raise ValueError(f"{name} must contain 1-512 characters")
        if not isinstance(source.content, str) or not source.content.strip():
            raise ValueError("source content must be non-empty")
        if len(source.content) > MAX_SOURCE_CHARS:
            raise ValueError("source content exceeds extraction size limit")
        if source.project_id is not None and (
            not isinstance(source.project_id, str) or not source.project_id.strip()
        ):
            raise ValueError("project_id must not be empty")

    @staticmethod
    def _validate_local_id(value: str) -> None:
        if not isinstance(value, str) or not value.strip() or len(value) > 128:
            raise ValueError("local_id must contain 1-128 non-whitespace characters")

    @staticmethod
    def _validate_confidence(value: float) -> None:
        if (
            isinstance(value, bool) or not isinstance(value, (int, float))
            or not math.isfinite(value) or not 0.0 <= value <= 1.0
        ):
            raise ValueError("confidence must be a finite number between 0 and 1")

    @staticmethod
    def _validate_evidence(refs: tuple[str, ...], source_ref: str) -> tuple[str, ...]:
        if not isinstance(refs, tuple) or not refs or len(refs) > MAX_EVIDENCE_REFS:
            raise ValueError("evidence_refs must contain 1-32 references")
        if any(not isinstance(ref, str) or not ref.strip() or len(ref) > 512 for ref in refs):
            raise ValueError("evidence references must be non-empty strings up to 512 characters")
        if source_ref not in refs:
            raise ValueError("evidence_refs must include the canonical source reference")
        if len(set(refs)) != len(refs):
            raise ValueError("evidence_refs must be unique")
        return refs

    @staticmethod
    def _candidate(
        *, source: GraphSourceRecord, kind: str, local_id: str,
        payload: dict[str, Any], confidence: float,
        evidence: tuple[str, ...], conflict_key: str | None,
    ) -> GraphExtractionCandidate:
        if conflict_key is not None and (
            not isinstance(conflict_key, str) or not conflict_key.strip() or len(conflict_key) > 256
        ):
            raise ValueError("conflict_key must contain 1-256 non-whitespace characters")
        canonical = json.dumps(
            {"kind": kind, "local_id": local_id, "payload": payload},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        identity = "\0".join((
            source.tenant_id, source.domain.value, source.source_type,
            source.source_id, source.source_version, kind, local_id, canonical,
        ))
        candidate_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if len(canonical) > MAX_METADATA_CHARS:
            raise ValueError("candidate payload exceeds size limit")
        return GraphExtractionCandidate(
            candidate_id=candidate_id, tenant_id=source.tenant_id,
            domain=source.domain, source_type=source.source_type,
            source_id=source.source_id, source_version=source.source_version,
            source_ref=source.source_ref, project_id=source.project_id,
            candidate_kind=kind, local_id=local_id, payload=payload,
            confidence=float(confidence), evidence_refs=evidence,
            conflict_key=conflict_key,
        )

    @staticmethod
    def _mark_batch_conflicts(
        candidates: list[GraphExtractionCandidate],
    ) -> list[GraphExtractionCandidate]:
        grouped: dict[str, set[str]] = {}
        for candidate in candidates:
            if candidate.conflict_key:
                fingerprint = json.dumps(candidate.payload, sort_keys=True, separators=(",", ":"))
                grouped.setdefault(candidate.conflict_key, set()).add(fingerprint)
        conflicts = {key for key, values in grouped.items() if len(values) > 1}
        return [
            GraphExtractionCandidate(**{
                **asdict(candidate),
                "status": "conflict" if candidate.conflict_key in conflicts else "candidate",
            })
            for candidate in candidates
        ]


class PostgresGraphCandidateStore:
    """Durable candidate queue; approval/materialization remains a separate step."""

    def __init__(self, *, tenant_id: str, connection_factory: Callable[[], Any]) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        self.tenant_id = tenant_id
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(cls, *, tenant_id: str, dsn: str) -> "PostgresGraphCandidateStore":
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")

        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)

        return cls(tenant_id=tenant_id, connection_factory=connect)

    async def append_candidates(self, candidates: Sequence[GraphExtractionCandidate]) -> int:
        if not candidates:
            return 0
        if any(candidate.tenant_id != self.tenant_id for candidate in candidates):
            raise ValueError("graph candidate tenant does not match store tenant")
        try:
            from psycopg.types.json import Jsonb
        except ImportError as exc:
            raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL storage") from exc

        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                inserted = 0
                for candidate in candidates:
                    cursor = await connection.execute(
                        """INSERT INTO platform_graph_extraction_candidates
                           (tenant_id, candidate_id, source_domain, source_type,
                            source_id, source_version, source_ref, project_id,
                            candidate_kind, local_id, payload, confidence,
                            evidence_refs, conflict_key, status)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                           ON CONFLICT (tenant_id, candidate_id) DO NOTHING
                           RETURNING candidate_id""",
                        (
                            self.tenant_id, candidate.candidate_id, candidate.domain.value,
                            candidate.source_type, candidate.source_id, candidate.source_version,
                            candidate.source_ref, candidate.project_id, candidate.candidate_kind,
                            candidate.local_id, Jsonb(candidate.payload), candidate.confidence,
                            Jsonb(list(candidate.evidence_refs)), candidate.conflict_key,
                            candidate.status,
                        ),
                    )
                    if await cursor.fetchone() is not None:
                        inserted += 1
        return inserted

    async def review_candidate(
        self, *, candidate_id: str, reviewer_id: str, decision: str, reason: str,
    ) -> bool:
        if not isinstance(candidate_id, str) or len(candidate_id) != 64:
            raise ValueError("candidate_id must be a 64-character SHA-256 digest")
        if not isinstance(reviewer_id, str) or not reviewer_id.strip():
            raise ValueError("reviewer_id is required")
        if decision not in {"accepted", "rejected"}:
            raise ValueError("decision must be accepted or rejected")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 1000:
            raise ValueError("review reason must contain 1-1000 characters")
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_graph_extraction_candidates
                       SET status = %s, reviewed_by = %s, review_reason = %s,
                           reviewed_at = now()
                       WHERE tenant_id = %s AND candidate_id = %s
                         AND status IN ('candidate', 'conflict')
                       RETURNING candidate_id""",
                    (decision, reviewer_id, reason.strip(), self.tenant_id, candidate_id),
                )
                return await cursor.fetchone() is not None
