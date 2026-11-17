"""Tenant-scoped PostgreSQL graph persistence and asynchronous index queue."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Mapping

from agent_platform.graph.schema import (
    GraphEntity,
    GraphRelation,
    GraphSchema,
    GraphSchemaRegistry,
    GraphSourceDomain,
)

ConnectionFactory = Callable[[], Any]
SchemaResolver = Callable[[str], GraphSchema]


def _json(value: Mapping[str, Any]) -> str:
    try:
        return json.dumps(dict(value), ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("graph attributes must be finite JSON values") from exc


def _source_mapping(schema: GraphSchema, domain: GraphSourceDomain, source_type: str):
    mapping = next(
        (
            item for item in schema.source_mappings
            if item.domain is domain and item.source_type == source_type
        ),
        None,
    )
    if mapping is None:
        raise ValueError("source domain/type is not mapped by the tenant graph schema")
    return mapping


def _authorized_projects(project_ids: set[str] | None) -> list[str]:
    if project_ids is None:
        return []
    if any(not isinstance(item, str) or not item.strip() for item in project_ids):
        raise ValueError("project_ids must contain non-empty strings")
    return sorted(project_ids)


@dataclass(frozen=True)
class GraphIndexJob:
    job_id: int
    tenant_id: str
    job_key: str
    object_kind: str
    object_id: str
    operation: str
    payload: Mapping[str, Any]
    attempts: int
    lease_owner: str | None
    lease_until: datetime | None


class PostgresGraphRepository:
    """Relational adjacency-list store with transactional durable index jobs.

    PostgreSQL is selected to reuse the platform's existing tenant-scoped
    database, transaction, backup and operations model; no extra graph server
    or vendor-specific runtime is introduced.
    """

    def __init__(
        self,
        connection_factory: ConnectionFactory,
        *,
        schema_resolver: SchemaResolver | None = None,
    ) -> None:
        self._connect = connection_factory
        self._resolve_schema = schema_resolver or GraphSchemaRegistry().get

    async def put_entity(
        self,
        entity: GraphEntity,
        *,
        source_domain: GraphSourceDomain,
        source_type: str,
        source_id: str,
        source_version: int,
    ) -> bool:
        schema = self._resolve_schema(entity.tenant_id)
        entity.validate(schema)
        if not isinstance(source_domain, GraphSourceDomain):
            raise ValueError("source_domain must be a GraphSourceDomain")
        mapping = _source_mapping(schema, source_domain, source_type)
        if mapping.entity_type != entity.entity_type:
            raise ValueError("source mapping entity type does not match graph entity")
        if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 200:
            raise ValueError("source_id must contain 1-200 non-whitespace characters")
        if type(source_version) is not int or source_version < 1:
            raise ValueError("source_version must be a positive integer")
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """INSERT INTO platform_graph_entities
                    (tenant_id,entity_id,entity_type,project_id,attributes,
                     source_domain,source_type,source_id,source_version)
                    VALUES(%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s)
                    ON CONFLICT(tenant_id,entity_id) DO UPDATE SET
                      project_id=EXCLUDED.project_id,
                      attributes=EXCLUDED.attributes,
                      updated_at=now()
                    WHERE platform_graph_entities.entity_type=EXCLUDED.entity_type
                      AND platform_graph_entities.source_domain=EXCLUDED.source_domain
                      AND platform_graph_entities.source_type=EXCLUDED.source_type
                      AND platform_graph_entities.source_id=EXCLUDED.source_id
                    RETURNING entity_id""",
                    (
                        entity.tenant_id, entity.entity_id, entity.entity_type,
                        entity.project_id, _json(entity.attributes), source_domain.value,
                        source_type, source_id, source_version,
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise ValueError("graph entity identity conflicts with an existing source")
                await self._enqueue(
                    conn, entity.tenant_id, "entity", entity.entity_id, "upsert",
                    {
                        "entity_type": entity.entity_type,
                        "project_id": entity.project_id,
                        "source_domain": source_domain.value,
                        "source_type": source_type,
                        "source_id": source_id,
                        "source_version": source_version,
                    },
                )
        return True

    async def put_relation(self, relation: GraphRelation) -> bool:
        schema = self._resolve_schema(relation.tenant_id)
        relation.validate(schema)
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """SELECT entity_id,entity_type,project_id FROM platform_graph_entities
                    WHERE tenant_id=%s AND entity_id=ANY(%s) FOR KEY SHARE""",
                    (
                        relation.tenant_id,
                        [relation.source_entity_id, relation.target_entity_id],
                    ),
                )
                endpoints = await cur.fetchall()
                expected_endpoints = len({
                    relation.source_entity_id, relation.target_entity_id,
                })
                if len(endpoints) != expected_endpoints:
                    raise ValueError("both relation endpoints must exist in the same tenant")
                allowed_projects = {None, relation.project_id}
                by_id = {row["entity_id"]: row for row in endpoints}
                source = by_id.get(relation.source_entity_id)
                target = by_id.get(relation.target_entity_id)
                if source is None or target is None:
                    raise ValueError("both relation endpoints must exist in the same tenant")
                if source["entity_type"] != relation.source_entity_type:
                    raise ValueError("source entity type does not match persisted endpoint")
                if target["entity_type"] != relation.target_entity_type:
                    raise ValueError("target entity type does not match persisted endpoint")
                if any(row["project_id"] not in allowed_projects for row in endpoints):
                    raise ValueError("relation endpoints cross project boundaries")
                if relation.project_id is None and any(
                    row["project_id"] is not None for row in endpoints
                ):
                    raise ValueError("project-scoped endpoints require a project-scoped relation")
                cur = await conn.execute(
                    """INSERT INTO platform_graph_relations
                    (tenant_id,relation_id,relation_type,project_id,
                     source_entity_id,target_entity_id,attributes)
                    VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT(tenant_id,relation_id) DO UPDATE SET
                      attributes=EXCLUDED.attributes,updated_at=now()
                    WHERE platform_graph_relations.relation_type=EXCLUDED.relation_type
                      AND platform_graph_relations.project_id IS NOT DISTINCT FROM EXCLUDED.project_id
                      AND platform_graph_relations.source_entity_id=EXCLUDED.source_entity_id
                      AND platform_graph_relations.target_entity_id=EXCLUDED.target_entity_id
                    RETURNING relation_id""",
                    (
                        relation.tenant_id, relation.relation_id, relation.relation_type,
                        relation.project_id, relation.source_entity_id,
                        relation.target_entity_id, _json(relation.attributes),
                    ),
                )
                row = await cur.fetchone()
                if row is None:
                    raise ValueError("graph relation identity conflicts with existing endpoints")
                await self._enqueue(
                    conn, relation.tenant_id, "relation", relation.relation_id, "upsert",
                    {
                        "relation_type": relation.relation_type,
                        "source_entity_id": relation.source_entity_id,
                        "target_entity_id": relation.target_entity_id,
                        "project_id": relation.project_id,
                    },
                )
        return True

    async def get_entity(
        self, tenant_id: str, entity_id: str, *, project_ids: set[str] | None = None,
    ) -> Mapping[str, Any] | None:
        if not tenant_id or not entity_id:
            raise ValueError("tenant_id and entity_id are required")
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """SELECT * FROM platform_graph_entities
                WHERE tenant_id=%s AND entity_id=%s
                  AND (project_id IS NULL OR project_id=ANY(%s))""",
                (tenant_id, entity_id, _authorized_projects(project_ids)),
            )
            return await cur.fetchone()

    async def list_entities(
        self,
        tenant_id: str,
        *,
        entity_type: str | None = None,
        project_ids: set[str] | None = None,
        limit: int = 100,
    ) -> list[Mapping[str, Any]]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        conditions = ["tenant_id=%s"]
        params: list[Any] = [tenant_id]
        if entity_type is not None:
            conditions.append("entity_type=%s")
            params.append(entity_type)
        conditions.append("(project_id IS NULL OR project_id=ANY(%s))")
        params.append(_authorized_projects(project_ids))
        params.append(limit)
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                "SELECT * FROM platform_graph_entities WHERE "
                + " AND ".join(conditions)
                + " ORDER BY updated_at DESC LIMIT %s",
                tuple(params),
            )
            return list(await cur.fetchall())

    async def _enqueue(
        self,
        conn: Any,
        tenant_id: str,
        object_kind: str,
        object_id: str,
        operation: str,
        payload: Mapping[str, Any],
    ) -> None:
        job_key = f"{object_kind}:{object_id}"
        await conn.execute(
            """INSERT INTO platform_graph_index_jobs
            (tenant_id,job_key,object_kind,object_id,operation,payload,status)
            VALUES(%s,%s,%s,%s,%s,%s::jsonb,'pending')
            ON CONFLICT(tenant_id,job_key) DO UPDATE SET
              operation=EXCLUDED.operation,payload=EXCLUDED.payload,
              status='pending',attempts=0,last_error_code=NULL,
              available_at=now(),lease_owner=NULL,lease_until=NULL,
              completed_at=NULL,updated_at=now()""",
            (tenant_id, job_key, object_kind, object_id, operation, _json(payload)),
        )

    async def claim_index_jobs(
        self, *, worker_id: str, limit: int = 20, lease_seconds: int = 60,
    ) -> list[GraphIndexJob]:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        conn = await self._connect()
        async with conn:
            async with conn.transaction():
                cur = await conn.execute(
                    """WITH dead_lettered AS (
                        UPDATE platform_graph_index_jobs
                        SET status='dead',lease_owner=NULL,lease_until=NULL,
                            last_error_code='lease_expired_max_attempts',updated_at=now()
                        WHERE status='processing' AND lease_until<=now() AND attempts >= 8
                    ), picked AS (
                        SELECT job_id FROM platform_graph_index_jobs
                        WHERE attempts < 8 AND (
                            (status='pending' AND available_at<=now())
                            OR (status='processing' AND lease_until<=now())
                        )
                        ORDER BY available_at,job_id
                        LIMIT %s FOR UPDATE SKIP LOCKED
                    )
                    UPDATE platform_graph_index_jobs AS job
                    SET status='processing',lease_owner=%s,
                        lease_until=now()+(%s * interval '1 second'),
                        attempts=attempts+1,updated_at=now()
                    FROM picked WHERE job.job_id=picked.job_id
                    RETURNING job.*""",
                    (limit, worker_id, lease_seconds),
                )
                rows = await cur.fetchall()
        return [
            GraphIndexJob(
                job_id=row["job_id"], tenant_id=row["tenant_id"],
                job_key=row["job_key"], object_kind=row["object_kind"],
                object_id=row["object_id"], operation=row["operation"],
                payload=dict(row["payload"] or {}), attempts=row["attempts"],
                lease_owner=row["lease_owner"], lease_until=row["lease_until"],
            )
            for row in rows
        ]

    async def complete_index_job(self, *, job_id: int, worker_id: str) -> bool:
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """UPDATE platform_graph_index_jobs
                SET status='completed',lease_owner=NULL,lease_until=NULL,
                    updated_at=now(),completed_at=now()
                WHERE job_id=%s AND status='processing' AND lease_owner=%s""",
                (job_id, worker_id),
            )
            return cur.rowcount == 1

    async def retry_index_job(
        self, *, job_id: int, worker_id: str, error_code: str,
        delay_seconds: int = 30, max_attempts: int = 8,
    ) -> bool:
        if not isinstance(error_code, str) or not error_code.strip() or len(error_code) > 80:
            raise ValueError("error_code must contain 1-80 characters")
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 3600:
            raise ValueError("delay_seconds must be between 1 and 3600")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 20:
            raise ValueError("max_attempts must be between 1 and 20")
        conn = await self._connect()
        async with conn:
            cur = await conn.execute(
                """UPDATE platform_graph_index_jobs
                SET status=CASE WHEN attempts >= %s THEN 'dead' ELSE 'pending' END,
                    available_at=now()+(%s * interval '1 second'),
                    last_error_code=%s,lease_owner=NULL,lease_until=NULL,updated_at=now()
                WHERE job_id=%s AND status='processing' AND lease_owner=%s""",
                (max_attempts, delay_seconds, error_code, job_id, worker_id),
            )
            return cur.rowcount == 1
