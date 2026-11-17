"""Durable PostgreSQL adapter for graph lifecycle governance.

This adapter persists versioned governance snapshots separately from the
canonical graph index. Host ingestion must call add_entity/add_relation when
projecting canonical graph records into the governance store.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Sequence

from agent_platform.graph.governance import (
    GraphEntityStatus,
    GraphGovernanceConflict,
    GraphInvalidationJob,
    GovernedGraphEntity,
    GovernedGraphRelation,
)

ConnectionFactory = Callable[[], Any]


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL graph governance") from exc
    return Jsonb(value)


def _entity(row: Mapping[str, Any]) -> GovernedGraphEntity:
    attributes = row["attributes"]
    source_refs = row["source_refs"]
    if isinstance(attributes, str):
        attributes = json.loads(attributes)
    if isinstance(source_refs, str):
        source_refs = json.loads(source_refs)
    return GovernedGraphEntity(
        tenant_id=row["tenant_id"], entity_id=row["entity_id"],
        entity_type=row["entity_type"], attributes=dict(attributes or {}),
        project_id=row["project_id"], source_refs=tuple(source_refs or ()),
        version=row["version"], status=GraphEntityStatus(row["status"]),
        expires_at=row["expires_at"], merged_into=row["merged_into"],
        updated_at=row["updated_at"], updated_by=row["updated_by"],
    )


def _relation(row: Mapping[str, Any]) -> GovernedGraphRelation:
    attributes = row["attributes"]
    source_refs = row["source_refs"]
    if isinstance(attributes, str):
        attributes = json.loads(attributes)
    if isinstance(source_refs, str):
        source_refs = json.loads(source_refs)
    return GovernedGraphRelation(
        tenant_id=row["tenant_id"], relation_id=row["relation_id"],
        relation_type=row["relation_type"], source_entity_id=row["source_entity_id"],
        target_entity_id=row["target_entity_id"], project_id=row["project_id"],
        attributes=dict(attributes or {}), source_refs=tuple(source_refs or ()),
        version=row["version"], status=GraphEntityStatus(row["status"]),
        updated_at=row["updated_at"],
    )


class PostgresGraphGovernanceRepository:
    """Tenant-bound, optimistic-CAS persistence for graph governance records."""

    def __init__(self, *, tenant_id: str, connection_factory: ConnectionFactory) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        self.tenant_id = tenant_id
        self._connect = connection_factory

    async def _lock(self, connection: Any, entity_ids: Sequence[str]) -> None:
        for entity_id in sorted(set(entity_ids)):
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{self.tenant_id}:{entity_id}",),
            )

    async def _audit(
        self, connection: Any, entity_id: str, action: str, actor_id: str,
        from_version: int, to_version: int, details: Mapping[str, Any],
    ) -> None:
        await connection.execute(
            """INSERT INTO platform_graph_governance_audit
               (tenant_id,entity_id,action,actor_id,from_version,to_version,details)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (self.tenant_id, entity_id, action, actor_id, from_version, to_version, _jsonb(dict(details))),
        )

    async def _history(self, connection: Any, entity: GovernedGraphEntity) -> None:
        await connection.execute(
            """INSERT INTO platform_graph_governed_entity_history
               (tenant_id,entity_id,version,entity_type,attributes,project_id,source_refs,
                status,expires_at,merged_into,updated_at,updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id,entity_id,version) DO NOTHING""",
            (entity.tenant_id, entity.entity_id, entity.version, entity.entity_type,
             _jsonb(dict(entity.attributes)), entity.project_id, _jsonb(list(entity.source_refs)),
             entity.status.value, entity.expires_at, entity.merged_into,
             entity.updated_at, entity.updated_by),
        )

    async def _save_entity(self, connection: Any, entity: GovernedGraphEntity) -> None:
        await connection.execute(
            """UPDATE platform_graph_governed_entities SET
                 attributes=%s,source_refs=%s,version=%s,status=%s,expires_at=%s,
                 merged_into=%s,updated_at=%s,updated_by=%s
               WHERE tenant_id=%s AND entity_id=%s""",
            (_jsonb(dict(entity.attributes)), _jsonb(list(entity.source_refs)), entity.version,
             entity.status.value, entity.expires_at, entity.merged_into, entity.updated_at,
             entity.updated_by, entity.tenant_id, entity.entity_id),
        )
        await self._history(connection, entity)

    async def add_entity(self, entity: GovernedGraphEntity) -> None:
        if entity.tenant_id != self.tenant_id:
            raise ValueError("entity tenant does not match repository tenant")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await self._lock(connection, (entity.entity_id,))
                await connection.execute(
                    """INSERT INTO platform_graph_governed_entities
                       (tenant_id,entity_id,entity_type,attributes,project_id,source_refs,
                        version,status,expires_at,merged_into,updated_at,updated_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (entity.tenant_id, entity.entity_id, entity.entity_type,
                     _jsonb(dict(entity.attributes)), entity.project_id,
                     _jsonb(list(entity.source_refs)), entity.version, entity.status.value,
                     entity.expires_at, entity.merged_into, entity.updated_at, entity.updated_by),
                )
                await self._history(connection, entity)

    async def add_relation(self, relation: GovernedGraphRelation) -> None:
        if relation.tenant_id != self.tenant_id:
            raise ValueError("relation tenant does not match repository tenant")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await self._lock(connection, (relation.source_entity_id, relation.target_entity_id))
                cursor = await connection.execute(
                    """SELECT entity_id,project_id,status FROM platform_graph_governed_entities
                       WHERE tenant_id=%s AND entity_id=ANY(%s) FOR KEY SHARE""",
                    (self.tenant_id, [relation.source_entity_id, relation.target_entity_id]),
                )
                endpoints = {row["entity_id"]: row for row in await cursor.fetchall()}
                if set(endpoints) != {relation.source_entity_id, relation.target_entity_id}:
                    raise ValueError("relation endpoints must exist in the same tenant")
                if any(row["status"] != "active" for row in endpoints.values()):
                    raise ValueError("relation endpoints must be active")
                if any(row["project_id"] not in (None, relation.project_id) for row in endpoints.values()):
                    raise PermissionError("relation endpoints cross project boundaries")
                await connection.execute(
                    """INSERT INTO platform_graph_governed_relations
                       (tenant_id,relation_id,relation_type,source_entity_id,target_entity_id,
                        project_id,attributes,source_refs,version,status,updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (relation.tenant_id, relation.relation_id, relation.relation_type,
                     relation.source_entity_id, relation.target_entity_id, relation.project_id,
                     _jsonb(dict(relation.attributes)), _jsonb(list(relation.source_refs)),
                     relation.version, relation.status.value, relation.updated_at),
                )
                await self._history_relation(connection, relation)

    async def _history_relation(self, connection: Any, relation: GovernedGraphRelation) -> None:
        await connection.execute(
            """INSERT INTO platform_graph_governed_relation_history
               (tenant_id,relation_id,version,relation_type,source_entity_id,target_entity_id,
                project_id,attributes,source_refs,status,updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id,relation_id,version) DO NOTHING""",
            (relation.tenant_id, relation.relation_id, relation.version, relation.relation_type,
             relation.source_entity_id, relation.target_entity_id, relation.project_id,
             _jsonb(dict(relation.attributes)), _jsonb(list(relation.source_refs)),
             relation.status.value, relation.updated_at),
        )

    async def get_entity(self, tenant_id: str, entity_id: str) -> GovernedGraphEntity | None:
        if tenant_id != self.tenant_id:
            return None
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_graph_governed_entities
                   WHERE tenant_id=%s AND entity_id=%s""", (self.tenant_id, entity_id),
            )
            row = await cursor.fetchone()
        return _entity(row) if row else None

    async def correct_entity(
        self, *, tenant_id: str, entity_id: str, expected_version: int,
        attributes: Mapping[str, Any], actor_id: str,
    ) -> GovernedGraphEntity:
        if tenant_id != self.tenant_id:
            raise KeyError("graph entity not found")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await self._lock(connection, (entity_id,))
                cursor = await connection.execute(
                    """SELECT * FROM platform_graph_governed_entities
                       WHERE tenant_id=%s AND entity_id=%s FOR UPDATE""",
                    (self.tenant_id, entity_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError("graph entity not found")
                current = _entity(row)
                self._check_version(current, expected_version)
                if current.status is not GraphEntityStatus.ACTIVE:
                    raise GraphGovernanceConflict("only active graph entities can be corrected")
                updated = GovernedGraphEntity(
                    current.tenant_id, current.entity_id, current.entity_type, attributes,
                    current.project_id, current.source_refs, current.version + 1,
                    current.status, current.expires_at, current.merged_into,
                    datetime.now(timezone.utc), actor_id,
                )
                await self._save_entity(connection, updated)
                await self._enqueue(connection, "entity", entity_id, "upsert", updated.version)
                await self._enqueue(connection, "cache", entity_id, "invalidate", updated.version)
                await self._audit(connection, entity_id, "correct", actor_id,
                                  current.version, updated.version,
                                  {"changed_properties": sorted(set(current.attributes) | set(attributes))})
                return updated

    async def transition_entity(
        self, *, tenant_id: str, entity_id: str, expected_version: int,
        status: GraphEntityStatus, actor_id: str,
    ) -> GovernedGraphEntity:
        if tenant_id != self.tenant_id:
            raise KeyError("graph entity not found")
        if status not in {GraphEntityStatus.ARCHIVED, GraphEntityStatus.EXPIRED, GraphEntityStatus.DELETED}:
            raise ValueError("unsupported governance transition")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await self._lock(connection, (entity_id,))
                cursor = await connection.execute(
                    """SELECT * FROM platform_graph_governed_entities
                       WHERE tenant_id=%s AND entity_id=%s FOR UPDATE""",
                    (self.tenant_id, entity_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise KeyError("graph entity not found")
                current = _entity(row)
                if current.status is status:
                    return current
                self._check_version(current, expected_version)
                allowed = ({GraphEntityStatus.ACTIVE, GraphEntityStatus.ARCHIVED, GraphEntityStatus.EXPIRED}
                           if status is GraphEntityStatus.DELETED else {GraphEntityStatus.ACTIVE})
                if current.status not in allowed:
                    raise GraphGovernanceConflict("graph entity cannot make this lifecycle transition")
                updated = GovernedGraphEntity(
                    current.tenant_id, current.entity_id, current.entity_type, current.attributes,
                    current.project_id, current.source_refs, current.version + 1, status,
                    current.expires_at, current.merged_into, datetime.now(timezone.utc), actor_id,
                )
                await self._save_entity(connection, updated)
                operation = "delete" if status is GraphEntityStatus.DELETED else "invalidate"
                await self._enqueue(connection, "entity", entity_id, operation, updated.version)
                await self._enqueue(connection, "cache", entity_id, "invalidate", updated.version)
                affected: list[str] = []
                if status is GraphEntityStatus.DELETED:
                    cursor = await connection.execute(
                        """SELECT * FROM platform_graph_governed_relations
                           WHERE tenant_id=%s AND (source_entity_id=%s OR target_entity_id=%s)
                             AND status='active' ORDER BY relation_id FOR UPDATE""",
                        (self.tenant_id, entity_id, entity_id),
                    )
                    for relation_row in await cursor.fetchall():
                        relation = _relation(relation_row)
                        deleted = GovernedGraphRelation(
                            relation.tenant_id, relation.relation_id, relation.relation_type,
                            relation.source_entity_id, relation.target_entity_id,
                            relation.project_id, relation.attributes, relation.source_refs,
                            relation.version + 1, GraphEntityStatus.DELETED,
                            datetime.now(timezone.utc),
                        )
                        await self._save_relation(connection, deleted)
                        affected.append(deleted.relation_id)
                        await self._enqueue(connection, "relation", deleted.relation_id, "delete", deleted.version)
                await self._audit(connection, entity_id, status.value, actor_id,
                                  current.version, updated.version,
                                  {"affected_relation_ids": sorted(affected)})
                return updated

    async def _save_relation(self, connection: Any, relation: GovernedGraphRelation) -> None:
        await connection.execute(
            """UPDATE platform_graph_governed_relations SET
                 source_entity_id=%s,target_entity_id=%s,attributes=%s,source_refs=%s,
                 version=%s,status=%s,updated_at=%s
               WHERE tenant_id=%s AND relation_id=%s""",
            (relation.source_entity_id, relation.target_entity_id, _jsonb(dict(relation.attributes)),
             _jsonb(list(relation.source_refs)), relation.version, relation.status.value,
             relation.updated_at, relation.tenant_id, relation.relation_id),
        )
        await self._history_relation(connection, relation)

    async def _enqueue(
        self, connection: Any, object_kind: str, object_id: str, operation: str, version: int,
    ) -> None:
        key = f"{self.tenant_id}:{object_kind}:{object_id}:v{version}"
        await connection.execute(
            """INSERT INTO platform_graph_invalidation_jobs
               (tenant_id,object_kind,object_id,operation,version,idempotency_key)
               VALUES (%s,%s,%s,%s,%s,%s)
               ON CONFLICT (tenant_id,idempotency_key) DO NOTHING""",
            (self.tenant_id, object_kind, object_id, operation, version, key),
        )

    async def _check_merge_entity(self, connection: Any, entity_id: str, expected_version: int):
        cursor = await connection.execute(
            """SELECT * FROM platform_graph_governed_entities
               WHERE tenant_id=%s AND entity_id=%s FOR UPDATE""",
            (self.tenant_id, entity_id),
        )
        row = await cursor.fetchone()
        if row is None:
            raise KeyError("source or target graph entity not found")
        entity = _entity(row)
        self._check_version(entity, expected_version)
        if entity.status is not GraphEntityStatus.ACTIVE:
            raise GraphGovernanceConflict("only active entities can be merged")
        return entity

    async def merge_entities(
        self, *, tenant_id: str, source_id: str, target_id: str,
        source_version: int, target_version: int, attributes: Mapping[str, Any],
        source_refs: tuple[str, ...], actor_id: str,
    ) -> GovernedGraphEntity:
        if tenant_id != self.tenant_id:
            raise KeyError("source or target graph entity not found")
        if source_id == target_id:
            raise ValueError("source and target entities must differ")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await self._lock(connection, (source_id, target_id))
                source = await self._check_merge_entity(connection, source_id, source_version)
                target = await self._check_merge_entity(connection, target_id, target_version)
                if source.entity_type != target.entity_type or source.project_id != target.project_id:
                    raise ValueError("merged entities must share type and project scope")
                now = datetime.now(timezone.utc)
                merged = GovernedGraphEntity(
                    target.tenant_id, target.entity_id, target.entity_type, attributes,
                    target.project_id, source_refs, target.version + 1, target.status,
                    target.expires_at, target.merged_into, now, actor_id,
                )
                superseded = GovernedGraphEntity(
                    source.tenant_id, source.entity_id, source.entity_type, source.attributes,
                    source.project_id, source.source_refs, source.version + 1,
                    GraphEntityStatus.SUPERSEDED, source.expires_at, target_id, now, actor_id,
                )
                await self._save_entity(connection, merged)
                await self._save_entity(connection, superseded)
                cursor = await connection.execute(
                    """SELECT * FROM platform_graph_governed_relations
                       WHERE tenant_id=%s AND status='active'
                         AND (source_entity_id=%s OR target_entity_id=%s)
                       ORDER BY relation_id FOR UPDATE""",
                    (self.tenant_id, source_id, source_id),
                )
                rows = await cursor.fetchall()
                cursor = await connection.execute(
                    """SELECT relation_type,source_entity_id,target_entity_id,project_id
                       FROM platform_graph_governed_relations
                       WHERE tenant_id=%s AND status='active'""", (self.tenant_id,),
                )
                existing = {
                    (r["relation_type"], r["source_entity_id"], r["target_entity_id"], r["project_id"])
                    for r in await cursor.fetchall()
                }
                for row in rows:
                    relation = _relation(row)
                    new_source = target_id if relation.source_entity_id == source_id else relation.source_entity_id
                    new_target = target_id if relation.target_entity_id == source_id else relation.target_entity_id
                    edge = (relation.relation_type, new_source, new_target, relation.project_id)
                    remove = new_source == new_target or (
                        edge in existing and (new_source != relation.source_entity_id or new_target != relation.target_entity_id)
                    )
                    updated = GovernedGraphRelation(
                        relation.tenant_id, relation.relation_id, relation.relation_type,
                        new_source, new_target, relation.project_id, relation.attributes,
                        relation.source_refs, relation.version + 1,
                        GraphEntityStatus.DELETED if remove else relation.status, now,
                    )
                    await self._save_relation(connection, updated)
                    if remove:
                        await self._enqueue(connection, "relation", relation.relation_id, "delete", updated.version)
                    else:
                        existing.add(edge)
                        await self._enqueue(connection, "relation", relation.relation_id, "upsert", updated.version)
                await self._enqueue(connection, "entity", target_id, "upsert", merged.version)
                await self._enqueue(connection, "entity", source_id, "delete", superseded.version)
                await self._enqueue(connection, "cache", target_id, "invalidate", merged.version)
                await self._enqueue(connection, "cache", source_id, "invalidate", superseded.version)
                await self._audit(connection, target_id, "merge_target", actor_id,
                                  target.version, merged.version, {"source_entity_id": source_id})
                await self._audit(connection, source_id, "merge_source", actor_id,
                                  source.version, superseded.version, {"target_entity_id": target_id})
                return merged

    async def expire_due(self, *, now: datetime, actor_id: str = "system:graph-expiry") -> int:
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """SELECT * FROM platform_graph_governed_entities
                       WHERE tenant_id=%s AND status='active' AND expires_at IS NOT NULL
                         AND expires_at<=%s ORDER BY expires_at,entity_id
                       FOR UPDATE SKIP LOCKED LIMIT 200""",
                    (self.tenant_id, now),
                )
                rows = await cursor.fetchall()
                count = 0
                for row in rows:
                    current = _entity(row)
                    updated = GovernedGraphEntity(
                        current.tenant_id, current.entity_id, current.entity_type, current.attributes,
                        current.project_id, current.source_refs, current.version + 1,
                        GraphEntityStatus.EXPIRED, current.expires_at, current.merged_into,
                        now, actor_id,
                    )
                    await self._save_entity(connection, updated)
                    await self._enqueue(connection, "entity", current.entity_id, "invalidate", updated.version)
                    await self._enqueue(connection, "cache", current.entity_id, "invalidate", updated.version)
                    await self._audit(connection, current.entity_id, "expired", actor_id,
                                      current.version, updated.version, {})
                    count += 1
                return count

    async def entity_history(self, tenant_id: str, entity_id: str):
        if tenant_id != self.tenant_id:
            return ()
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_graph_governed_entity_history
                   WHERE tenant_id=%s AND entity_id=%s ORDER BY version""",
                (self.tenant_id, entity_id),
            )
            return tuple(_entity(row) for row in await cursor.fetchall())

    async def relation_history(self, tenant_id: str, relation_id: str):
        if tenant_id != self.tenant_id:
            return ()
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_graph_governed_relation_history
                   WHERE tenant_id=%s AND relation_id=%s ORDER BY version""",
                (self.tenant_id, relation_id),
            )
            return tuple(_relation(row) for row in await cursor.fetchall())

    async def audit_events(self, tenant_id: str, entity_id: str | None = None, *, limit: int = 100):
        if tenant_id != self.tenant_id:
            return ()
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_graph_governance_audit
                   WHERE tenant_id=%s AND (%s IS NULL OR entity_id=%s)
                   ORDER BY occurred_at,event_id LIMIT %s""",
                (self.tenant_id, entity_id, entity_id, limit),
            )
            return tuple(await cursor.fetchall())

    async def claim_invalidation_jobs(
        self, *, worker_id: str, limit: int = 20, lease_seconds: int = 60,
    ):
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """WITH picked AS (
                         SELECT job_id FROM platform_graph_invalidation_jobs
                         WHERE tenant_id=%s AND attempts<8
                           AND ((status='pending' AND available_at<=now())
                             OR (status='processing' AND lease_until<=now()))
                         ORDER BY available_at,job_id FOR UPDATE SKIP LOCKED LIMIT %s
                       )
                       UPDATE platform_graph_invalidation_jobs j
                       SET status='processing',attempts=j.attempts+1,lease_owner=%s,
                           lease_until=now()+(%s * interval '1 second')
                       FROM picked WHERE j.job_id=picked.job_id RETURNING j.*""",
                    (self.tenant_id, limit, worker_id, lease_seconds),
                )
                rows = await cursor.fetchall()
                return tuple(GraphInvalidationJob(
                    job_id=str(row["job_id"]), tenant_id=row["tenant_id"],
                    object_kind=row["object_kind"], object_id=row["object_id"],
                    operation=row["operation"], idempotency_key=row["idempotency_key"],
                    status=row["status"], attempts=row["attempts"],
                    available_at=row["available_at"], lease_owner=row["lease_owner"],
                    lease_until=row["lease_until"],
                ) for row in rows)

    async def complete_invalidation_job(self, *, job_id: str, worker_id: str) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_graph_invalidation_jobs
                   SET status='completed',lease_owner=NULL,lease_until=NULL,completed_at=now()
                   WHERE tenant_id=%s AND job_id=%s AND status='processing'
                     AND lease_owner=%s AND lease_until>now() RETURNING job_id""",
                (self.tenant_id, job_id, worker_id),
            )
            return await cursor.fetchone() is not None

    async def retry_invalidation_job(
        self, *, job_id: str, worker_id: str, delay_seconds: int = 30,
    ) -> bool:
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 3600:
            raise ValueError("delay_seconds must be between 1 and 3600")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """UPDATE platform_graph_invalidation_jobs
                   SET status=CASE WHEN attempts>=8 THEN 'dead' ELSE 'pending' END,
                       available_at=now()+(%s * interval '1 second'),
                       lease_owner=NULL,lease_until=NULL
                   WHERE tenant_id=%s AND job_id=%s AND status='processing'
                     AND lease_owner=%s RETURNING job_id""",
                (delay_seconds, self.tenant_id, job_id, worker_id),
            )
            return await cursor.fetchone() is not None

    @staticmethod
    def _check_version(entity: GovernedGraphEntity, expected_version: int) -> None:
        if type(expected_version) is not int or entity.version != expected_version:
            raise GraphGovernanceConflict("graph entity version conflict")
