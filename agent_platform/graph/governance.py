"""Graph lifecycle governance, lineage, audit and derived-data invalidation."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from enum import Enum
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from agent_platform.graph.schema import GraphSchema
from agent_platform.knowledge.contracts import KnowledgePrincipal


class GraphEntityStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    EXPIRED = "expired"
    DELETED = "deleted"
    SUPERSEDED = "superseded"


class GraphGovernanceConflict(RuntimeError):
    """Optimistic version or lifecycle conflict."""


_SOURCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _validate_source_reference(value: str, tenant_id: str) -> None:
    if not isinstance(value, str) or value != value.strip() or not value:
        raise ValueError("source reference must be a canonical non-empty URI")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"knowledge", "memory", "blackboard"}
        or parsed.netloc != tenant_id
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.count("/") != 1
        or not parsed.path.startswith("/")
    ):
        raise ValueError("source reference must be tenant-bound to a canonical graph source")
    record_version = parsed.path[1:].rsplit("@", 1)
    if len(record_version) != 2:
        raise ValueError("source reference must pin an explicit version")
    record_id, raw_version = record_version
    if (
        not _SOURCE_ID.fullmatch(record_id)
        or not raw_version.isdecimal()
        or str(int(raw_version)) != raw_version
        or int(raw_version) < 1
    ):
        raise ValueError("source reference record and version must be canonical")


@dataclass(frozen=True)
class GovernedGraphEntity:
    tenant_id: str
    entity_id: str
    entity_type: str
    attributes: Mapping[str, Any]
    project_id: str | None = None
    source_refs: tuple[str, ...] = ()
    version: int = 1
    status: GraphEntityStatus = GraphEntityStatus.ACTIVE
    expires_at: datetime | None = None
    merged_into: str | None = None
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_by: str = "system"

    def __post_init__(self) -> None:
        for name in ("tenant_id", "entity_id", "entity_type", "updated_by"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if self.project_id is not None and (
            not isinstance(self.project_id, str) or not self.project_id.strip()
        ):
            raise ValueError("project_id must be a non-empty string")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if not isinstance(self.status, GraphEntityStatus):
            raise ValueError("status must be a GraphEntityStatus")
        if not isinstance(self.attributes, Mapping):
            raise ValueError("attributes must be a mapping")
        if not isinstance(self.source_refs, tuple):
            raise ValueError("source_refs must be a tuple of canonical references")
        for reference in self.source_refs:
            _validate_source_reference(reference, self.tenant_id)
        if self.expires_at is not None and (
            not isinstance(self.expires_at, datetime)
            or self.expires_at.tzinfo is None
            or self.expires_at.utcoffset() is None
        ):
            raise ValueError("expires_at must be timezone-aware")
        if (
            not isinstance(self.updated_at, datetime)
            or self.updated_at.tzinfo is None
            or self.updated_at.utcoffset() is None
        ):
            raise ValueError("updated_at must be timezone-aware")
        object.__setattr__(self, "attributes", MappingProxyType(deepcopy(dict(self.attributes))))


@dataclass(frozen=True)
class GovernedGraphRelation:
    tenant_id: str
    relation_id: str
    relation_type: str
    source_entity_id: str
    target_entity_id: str
    project_id: str | None = None
    attributes: Mapping[str, Any] = field(default_factory=dict)
    source_refs: tuple[str, ...] = ()
    version: int = 1
    status: GraphEntityStatus = GraphEntityStatus.ACTIVE
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        for name in (
            "tenant_id", "relation_id", "relation_type",
            "source_entity_id", "target_entity_id",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        if not isinstance(self.status, GraphEntityStatus):
            raise ValueError("status must be a GraphEntityStatus")
        if self.project_id is not None and (
            not isinstance(self.project_id, str) or not self.project_id.strip()
        ):
            raise ValueError("project_id must be a non-empty string")
        if not isinstance(self.source_refs, tuple):
            raise ValueError("source_refs must be a tuple of canonical references")
        for reference in self.source_refs:
            _validate_source_reference(reference, self.tenant_id)
        if not isinstance(self.updated_at, datetime) or self.updated_at.tzinfo is None:
            raise ValueError("updated_at must be timezone-aware")
        object.__setattr__(self, "attributes", MappingProxyType(deepcopy(dict(self.attributes))))


@dataclass(frozen=True)
class GraphGovernanceAuditEvent:
    event_id: str
    tenant_id: str
    entity_id: str
    action: str
    actor_id: str
    from_version: int
    to_version: int
    occurred_at: datetime
    details: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(deepcopy(dict(self.details))))


@dataclass(frozen=True)
class GraphInvalidationJob:
    job_id: str
    tenant_id: str
    object_kind: str
    object_id: str
    operation: str
    idempotency_key: str
    status: str = "pending"
    attempts: int = 0
    available_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lease_owner: str | None = None
    lease_until: datetime | None = None


class GraphGovernanceRepository(Protocol):
    async def get_entity(self, tenant_id: str, entity_id: str) -> GovernedGraphEntity | None: ...
    async def correct_entity(self, *, tenant_id: str, entity_id: str, expected_version: int,
                             attributes: Mapping[str, Any], actor_id: str) -> GovernedGraphEntity: ...
    async def transition_entity(self, *, tenant_id: str, entity_id: str, expected_version: int,
                                status: GraphEntityStatus, actor_id: str) -> GovernedGraphEntity: ...
    async def merge_entities(self, *, tenant_id: str, source_id: str, target_id: str,
                             source_version: int, target_version: int,
                             attributes: Mapping[str, Any], actor_id: str) -> GovernedGraphEntity: ...


class InMemoryGraphGovernanceRepository:
    """Atomic reference adapter; persistent adapters implement the same contract."""

    def __init__(self) -> None:
        self._entities: dict[tuple[str, str], GovernedGraphEntity] = {}
        self._relations: dict[tuple[str, str], GovernedGraphRelation] = {}
        self._entity_history: dict[tuple[str, str], list[GovernedGraphEntity]] = {}
        self._relation_history: dict[tuple[str, str], list[GovernedGraphRelation]] = {}
        self._audit: list[GraphGovernanceAuditEvent] = []
        self._jobs: dict[str, GraphInvalidationJob] = {}
        self._lock = asyncio.Lock()

    async def add_entity(self, entity: GovernedGraphEntity) -> None:
        async with self._lock:
            key = (entity.tenant_id, entity.entity_id)
            if key in self._entities:
                raise GraphGovernanceConflict("graph entity already exists")
            self._entities[key] = entity
            self._entity_history[key] = [entity]

    async def add_relation(self, relation: GovernedGraphRelation) -> None:
        async with self._lock:
            key = (relation.tenant_id, relation.relation_id)
            source = self._entities.get((relation.tenant_id, relation.source_entity_id))
            target = self._entities.get((relation.tenant_id, relation.target_entity_id))
            if source is None or target is None:
                raise ValueError("relation endpoints must exist in the same tenant")
            if source.status is not GraphEntityStatus.ACTIVE or target.status is not GraphEntityStatus.ACTIVE:
                raise ValueError("relation endpoints must be active")
            if source.project_id not in (None, relation.project_id) or target.project_id not in (
                None, relation.project_id
            ):
                raise ValueError("relation endpoints cross project boundaries")
            if key in self._relations:
                raise GraphGovernanceConflict("graph relation already exists")
            self._relations[key] = relation
            self._relation_history[key] = [relation]

    async def get_entity(self, tenant_id: str, entity_id: str) -> GovernedGraphEntity | None:
        async with self._lock:
            return self._entities.get((tenant_id, entity_id))

    async def get_relations(self, tenant_id: str, *, include_inactive: bool = False):
        async with self._lock:
            return tuple(
                item for (tenant, _), item in self._relations.items()
                if tenant == tenant_id and (
                    include_inactive or item.status is GraphEntityStatus.ACTIVE
                )
            )

    async def correct_entity(
        self, *, tenant_id: str, entity_id: str, expected_version: int,
        attributes: Mapping[str, Any], actor_id: str,
    ) -> GovernedGraphEntity:
        async with self._lock:
            key = (tenant_id, entity_id)
            current = self._entities.get(key)
            self._check_current(current, expected_version)
            if current.status is not GraphEntityStatus.ACTIVE:
                raise GraphGovernanceConflict("only active graph entities can be corrected")
            updated = replace(
                current, attributes=attributes, version=current.version + 1,
                updated_at=datetime.now(timezone.utc), updated_by=actor_id,
            )
            self._save_entity(key, updated)
            self._enqueue(tenant_id, "entity", entity_id, "upsert", updated.version)
            self._enqueue(tenant_id, "cache", entity_id, "invalidate", updated.version)
            self._record_audit(
                tenant_id, entity_id, "correct", actor_id,
                current.version, updated.version,
                {"changed_properties": sorted(set(current.attributes) | set(attributes))},
            )
            return updated

    async def transition_entity(
        self, *, tenant_id: str, entity_id: str, expected_version: int,
        status: GraphEntityStatus, actor_id: str,
    ) -> GovernedGraphEntity:
        if status not in {
            GraphEntityStatus.ARCHIVED, GraphEntityStatus.EXPIRED, GraphEntityStatus.DELETED,
        }:
            raise ValueError("unsupported governance transition")
        async with self._lock:
            key = (tenant_id, entity_id)
            current = self._entities.get(key)
            if current is None:
                raise KeyError("graph entity not found")
            if current.status is status:
                return current
            self._check_current(current, expected_version)
            allowed_from = (
                {GraphEntityStatus.ACTIVE, GraphEntityStatus.ARCHIVED, GraphEntityStatus.EXPIRED}
                if status is GraphEntityStatus.DELETED
                else {GraphEntityStatus.ACTIVE}
            )
            if current.status not in allowed_from:
                raise GraphGovernanceConflict("graph entity cannot make this lifecycle transition")
            updated = replace(
                current, status=status, version=current.version + 1,
                updated_at=datetime.now(timezone.utc), updated_by=actor_id,
            )
            self._save_entity(key, updated)
            operation = "delete" if status is GraphEntityStatus.DELETED else "invalidate"
            self._enqueue(tenant_id, "entity", entity_id, operation, updated.version)
            self._enqueue(tenant_id, "cache", entity_id, "invalidate", updated.version)
            affected_relations: list[str] = []
            if status is GraphEntityStatus.DELETED:
                for relation_key, relation in tuple(self._relations.items()):
                    if relation.tenant_id != tenant_id or (
                        relation.source_entity_id != entity_id
                        and relation.target_entity_id != entity_id
                    ):
                        continue
                    deleted = replace(
                        relation, status=GraphEntityStatus.DELETED,
                        version=relation.version + 1, updated_at=datetime.now(timezone.utc),
                    )
                    self._save_relation(relation_key, deleted)
                    affected_relations.append(relation.relation_id)
                    self._enqueue(tenant_id, "relation", relation.relation_id, "delete", deleted.version)
            self._record_audit(
                tenant_id, entity_id, status.value, actor_id,
                current.version, updated.version,
                {"affected_relation_ids": sorted(affected_relations)},
            )
            return updated

    async def merge_entities(
        self, *, tenant_id: str, source_id: str, target_id: str,
        source_version: int, target_version: int, attributes: Mapping[str, Any],
        actor_id: str,
    ) -> GovernedGraphEntity:
        if source_id == target_id:
            raise ValueError("source and target entities must differ")
        async with self._lock:
            source_key = (tenant_id, source_id)
            target_key = (tenant_id, target_id)
            source = self._entities.get(source_key)
            target = self._entities.get(target_key)
            self._check_current(source, source_version)
            self._check_current(target, target_version)
            if source.status is not GraphEntityStatus.ACTIVE or target.status is not GraphEntityStatus.ACTIVE:
                raise GraphGovernanceConflict("only active entities can be merged")
            if source.entity_type != target.entity_type or source.project_id != target.project_id:
                raise ValueError("merged entities must share type and project scope")
            merged = replace(
                target, attributes=attributes, version=target.version + 1,
                updated_at=datetime.now(timezone.utc), updated_by=actor_id,
            )
            superseded = replace(
                source, status=GraphEntityStatus.SUPERSEDED,
                merged_into=target_id, version=source.version + 1,
                updated_at=datetime.now(timezone.utc), updated_by=actor_id,
            )
            self._save_entity(target_key, merged)
            self._save_entity(source_key, superseded)
            seen_edges: set[tuple[str, str, str, str | None]] = set()
            ordered_relations = sorted(
                self._relations.items(),
                key=lambda item: (
                    item[1].source_entity_id == source_id
                    or item[1].target_entity_id == source_id,
                    item[1].relation_id,
                ),
            )
            for relation_key, relation in ordered_relations:
                if relation.tenant_id != tenant_id or relation.status is not GraphEntityStatus.ACTIVE:
                    continue
                new_source = target_id if relation.source_entity_id == source_id else relation.source_entity_id
                new_target = target_id if relation.target_entity_id == source_id else relation.target_entity_id
                if new_source == new_target:
                    self._delete_relation(relation_key, relation, tenant_id)
                    continue
                edge_key = (relation.relation_type, new_source, new_target, relation.project_id)
                if edge_key in seen_edges:
                    self._delete_relation(relation_key, relation, tenant_id)
                    continue
                seen_edges.add(edge_key)
                if new_source != relation.source_entity_id or new_target != relation.target_entity_id:
                    rewired = replace(
                        relation, source_entity_id=new_source, target_entity_id=new_target,
                        version=relation.version + 1, updated_at=datetime.now(timezone.utc),
                    )
                    self._save_relation(relation_key, rewired)
                    self._enqueue(tenant_id, "relation", relation.relation_id, "upsert", rewired.version)
            self._enqueue(tenant_id, "entity", target_id, "upsert", merged.version)
            self._enqueue(tenant_id, "entity", source_id, "delete", superseded.version)
            self._enqueue(tenant_id, "cache", target_id, "invalidate", merged.version)
            self._enqueue(tenant_id, "cache", source_id, "invalidate", superseded.version)
            self._record_audit(
                tenant_id, target_id, "merge_target", actor_id,
                target.version, merged.version, {"source_entity_id": source_id},
            )
            self._record_audit(
                tenant_id, source_id, "merge_source", actor_id,
                source.version, superseded.version, {"target_entity_id": target_id},
            )
            return merged

    async def expire_due(
        self, *, now: datetime, actor_id: str = "system:graph-expiry",
    ) -> int:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        async with self._lock:
            due = [
                entity for entity in self._entities.values()
                if entity.status is GraphEntityStatus.ACTIVE
                and entity.expires_at is not None and entity.expires_at <= now
            ]
        count = 0
        for entity in due:
            try:
                await self.transition_entity(
                    tenant_id=entity.tenant_id, entity_id=entity.entity_id,
                    expected_version=entity.version, status=GraphEntityStatus.EXPIRED,
                    actor_id=actor_id,
                )
                count += 1
            except GraphGovernanceConflict:
                continue
        return count

    async def entity_history(self, tenant_id: str, entity_id: str):
        async with self._lock:
            return tuple(self._entity_history.get((tenant_id, entity_id), ()))

    async def relation_history(self, tenant_id: str, relation_id: str):
        async with self._lock:
            return tuple(self._relation_history.get((tenant_id, relation_id), ()))

    async def audit_events(self, tenant_id: str, entity_id: str | None = None):
        async with self._lock:
            return tuple(
                event for event in self._audit
                if event.tenant_id == tenant_id and (
                    entity_id is None or event.entity_id == entity_id
                )
            )

    async def invalidation_jobs(self, tenant_id: str):
        async with self._lock:
            return tuple(job for job in self._jobs.values() if job.tenant_id == tenant_id)

    async def claim_invalidation_jobs(
        self, *, worker_id: str, limit: int = 20, lease_seconds: int = 60,
        now: datetime | None = None,
    ) -> tuple[GraphInvalidationJob, ...]:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        instant = now or datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        async with self._lock:
            claimed: list[GraphInvalidationJob] = []
            for key, job in sorted(self._jobs.items(), key=lambda item: item[1].available_at):
                expired = job.status == "processing" and job.lease_until is not None and job.lease_until <= instant
                ready = job.status == "pending" and job.available_at <= instant
                if not (ready or expired):
                    continue
                if job.attempts >= 8:
                    self._jobs[key] = replace(
                        job, status="dead", lease_owner=None, lease_until=None,
                    )
                    continue
                updated = replace(
                    job, status="processing", attempts=job.attempts + 1,
                    lease_owner=worker_id, lease_until=instant + timedelta(seconds=lease_seconds),
                )
                self._jobs[key] = updated
                claimed.append(updated)
                if len(claimed) >= limit:
                    break
            return tuple(claimed)

    async def complete_invalidation_job(self, *, job_id: str, worker_id: str) -> bool:
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "processing" or job.lease_owner != worker_id:
                return False
            self._jobs[job_id] = replace(job, status="completed", lease_owner=None, lease_until=None)
            return True

    async def retry_invalidation_job(
        self, *, job_id: str, worker_id: str, delay_seconds: int = 30,
        now: datetime | None = None,
    ) -> bool:
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 3600:
            raise ValueError("delay_seconds must be between 1 and 3600")
        instant = now or datetime.now(timezone.utc)
        if not isinstance(instant, datetime) or instant.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        async with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status != "processing" or job.lease_owner != worker_id:
                return False
            status = "dead" if job.attempts >= 8 else "pending"
            self._jobs[job_id] = replace(
                job, status=status, available_at=instant + timedelta(seconds=delay_seconds),
                lease_owner=None, lease_until=None,
            )
            return True

    def _save_entity(self, key, entity: GovernedGraphEntity) -> None:
        self._entities[key] = entity
        self._entity_history.setdefault(key, []).append(entity)

    def _save_relation(self, key, relation: GovernedGraphRelation) -> None:
        self._relations[key] = relation
        self._relation_history.setdefault(key, []).append(relation)

    def _delete_relation(self, key, relation: GovernedGraphRelation, tenant_id: str) -> None:
        deleted = replace(
            relation, status=GraphEntityStatus.DELETED,
            version=relation.version + 1, updated_at=datetime.now(timezone.utc),
        )
        self._save_relation(key, deleted)
        self._enqueue(tenant_id, "relation", relation.relation_id, "delete", deleted.version)

    def _enqueue(
        self, tenant_id: str, object_kind: str, object_id: str,
        operation: str, version: int,
    ) -> None:
        key = f"{tenant_id}:{object_kind}:{object_id}"
        self._jobs[key] = GraphInvalidationJob(
            job_id=key, tenant_id=tenant_id, object_kind=object_kind,
            object_id=object_id, operation=operation,
            idempotency_key=f"{key}:v{version}",
        )

    def _record_audit(
        self, tenant_id: str, entity_id: str, action: str, actor_id: str,
        from_version: int, to_version: int, details: Mapping[str, Any],
    ) -> None:
        self._audit.append(GraphGovernanceAuditEvent(
            event_id=str(uuid4()), tenant_id=tenant_id, entity_id=entity_id,
            action=action, actor_id=actor_id, from_version=from_version,
            to_version=to_version, occurred_at=datetime.now(timezone.utc),
            details=details,
        ))

    @staticmethod
    def _check_current(entity: GovernedGraphEntity | None, expected_version: int) -> None:
        if entity is None:
            raise KeyError("graph entity not found")
        if type(expected_version) is not int or entity.version != expected_version:
            raise GraphGovernanceConflict("graph entity version conflict")


class GraphGovernanceService:
    def __init__(
        self,
        repository: GraphGovernanceRepository,
        *,
        schema_resolver: Callable[[str], GraphSchema],
    ) -> None:
        self.repository = repository
        self._schema_for = schema_resolver

    @staticmethod
    def _authorize(principal: KnowledgePrincipal, entity: GovernedGraphEntity) -> str:
        if not isinstance(principal, KnowledgePrincipal) or not principal.user_id:
            raise PermissionError("authenticated graph governance principal required")
        if principal.tenant_id != entity.tenant_id:
            raise PermissionError("cross-tenant graph governance is forbidden")
        if entity.project_id is not None:
            if (
                entity.project_id not in principal.manage_project_ids
                and "graph.manage" not in principal.permissions
            ):
                raise PermissionError("project graph.manage permission required")
        elif not {"graph.manage", "knowledge.manage"} & principal.permissions:
            raise PermissionError("tenant graph.manage permission required")
        return principal.user_id

    async def correct_entity(
        self, *, principal: KnowledgePrincipal, entity_id: str,
        expected_version: int, attributes: Mapping[str, Any],
    ) -> GovernedGraphEntity:
        current = await self.repository.get_entity(principal.tenant_id, entity_id)
        if current is None:
            raise KeyError("graph entity not found")
        actor = self._authorize(principal, current)
        schema = self._schema_for(current.tenant_id)
        schema.validate_attributes(
            schema.entity_type(current.entity_type).properties, attributes,
        )
        return await self.repository.correct_entity(
            tenant_id=current.tenant_id, entity_id=entity_id,
            expected_version=expected_version, attributes=attributes, actor_id=actor,
        )

    async def archive_entity(
        self, *, principal: KnowledgePrincipal, entity_id: str, expected_version: int,
    ) -> GovernedGraphEntity:
        return await self._transition(
            principal=principal, entity_id=entity_id,
            expected_version=expected_version, status=GraphEntityStatus.ARCHIVED,
        )

    async def delete_entity(
        self, *, principal: KnowledgePrincipal, entity_id: str, expected_version: int,
    ) -> GovernedGraphEntity:
        return await self._transition(
            principal=principal, entity_id=entity_id,
            expected_version=expected_version, status=GraphEntityStatus.DELETED,
        )

    async def _transition(
        self, *, principal: KnowledgePrincipal, entity_id: str,
        expected_version: int, status: GraphEntityStatus,
    ) -> GovernedGraphEntity:
        current = await self.repository.get_entity(principal.tenant_id, entity_id)
        if current is None:
            raise KeyError("graph entity not found")
        actor = self._authorize(principal, current)
        return await self.repository.transition_entity(
            tenant_id=current.tenant_id, entity_id=entity_id,
            expected_version=expected_version, status=status, actor_id=actor,
        )

    async def merge_entities(
        self, *, principal: KnowledgePrincipal, source_id: str, target_id: str,
        source_version: int, target_version: int, conflict_policy: str = "prefer_target",
    ) -> GovernedGraphEntity:
        source = await self.repository.get_entity(principal.tenant_id, source_id)
        target = await self.repository.get_entity(principal.tenant_id, target_id)
        if source is None or target is None:
            raise KeyError("source or target graph entity not found")
        actor = self._authorize(principal, source)
        self._authorize(principal, target)
        if conflict_policy not in {"prefer_target", "prefer_source"}:
            raise ValueError("conflict_policy must be prefer_target or prefer_source")
        merged = dict(source.attributes)
        if conflict_policy == "prefer_target":
            merged.update(target.attributes)
        else:
            merged.update(target.attributes)
            merged.update(source.attributes)
        schema = self._schema_for(target.tenant_id)
        schema.validate_attributes(schema.entity_type(target.entity_type).properties, merged)
        return await self.repository.merge_entities(
            tenant_id=source.tenant_id, source_id=source_id, target_id=target_id,
            source_version=source_version, target_version=target_version,
            attributes=merged, actor_id=actor,
        )

    async def expire_due(self, *, now: datetime | None = None) -> int:
        expiry = getattr(self.repository, "expire_due", None)
        if not callable(expiry):
            raise RuntimeError("repository does not support expiry sweeps")
        return await expiry(now=now or datetime.now(timezone.utc))
