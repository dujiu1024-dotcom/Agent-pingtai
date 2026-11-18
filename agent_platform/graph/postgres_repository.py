"""Tenant/project-scoped PostgreSQL storage for the knowledge graph.

PostgreSQL is the initial graph backend: adjacency is represented by relation
rows and maintained transactionally with entity writes. Native B-tree/GIN
indexes are updated synchronously by PostgreSQL; no external index queue can
drift from the canonical graph tables.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence

from agent_platform.graph.schema import (
    GraphEntity,
    GraphRelation,
    GraphSchema,
    GraphSourceDomain,
    default_graph_schema,
)

ConnectionFactory = Callable[[], Any]


@dataclass(frozen=True)
class GraphSourceReference:
    domain: GraphSourceDomain
    source_type: str
    source_id: str
    source_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.domain, GraphSourceDomain):
            raise ValueError("domain must be a GraphSourceDomain")
        for name in ("source_type", "source_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError(f"{name} must contain 1-200 non-whitespace characters")
        if (
            isinstance(self.source_version, bool)
            or not isinstance(self.source_version, int)
            or self.source_version < 1
        ):
            raise ValueError("source_version must be a positive integer")


@dataclass(frozen=True)
class GraphEntityRecord:
    entity: GraphEntity
    name: str
    source_ref: GraphSourceReference | None
    created_at: Any
    updated_at: Any


@dataclass(frozen=True)
class GraphRelationRecord:
    relation: GraphRelation
    created_at: Any
    updated_at: Any


def _jsonb(value: Any) -> Any:
    try:
        from psycopg.types.json import Jsonb
    except ImportError as exc:
        raise RuntimeError("Install psycopg[binary,pool] for PostgreSQL graph storage") from exc
    return Jsonb(value)


class PostgresGraphRepository:
    """PostgreSQL graph repository with tenant-bound queries and project filters.

    The host must authenticate the caller and pass only project IDs the caller
    may access. This repository adds tenant predicates and applies that project
    allow-list; it is not a replacement for the platform authorization service.
    """

    def __init__(
        self,
        *,
        tenant_id: str,
        connection_factory: ConnectionFactory,
        schema: GraphSchema | None = None,
    ) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if schema is not None and schema.tenant_id != tenant_id:
            raise ValueError("schema tenant does not match repository tenant")
        self.tenant_id = tenant_id
        self._connect = connection_factory
        self.schema = schema or default_graph_schema(tenant_id)

    @classmethod
    def from_dsn(
        cls, *, tenant_id: str, dsn: str, schema: GraphSchema | None = None
    ) -> "PostgresGraphRepository":
        if not dsn:
            raise ValueError("PostgreSQL DSN is required")

        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
            except ImportError as exc:
                raise RuntimeError(
                    "Install psycopg[binary,pool] for PostgreSQL graph storage"
                ) from exc
            return await AsyncConnection.connect(dsn, row_factory=dict_row)

        return cls(tenant_id=tenant_id, connection_factory=connect, schema=schema)

    async def _lock_entity_ids(self, connection: Any, entity_ids: Sequence[str]) -> None:
        """Serialize graph writes/deletes for endpoint IDs in deterministic order."""
        for entity_id in sorted(set(entity_ids)):
            await connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                (f"{self.tenant_id}:{entity_id}",),
            )

    def _check_project_write(self, project_id: str | None, allowed_project_ids: Sequence[str]) -> None:
        if project_id is not None and project_id not in set(allowed_project_ids):
            raise PermissionError("project is outside the authorized graph write scope")

    def _check_source_ref(
        self, entity_type: str, source_ref: GraphSourceReference | None
    ) -> None:
        if source_ref is None:
            return
        mapping = next(
            (
                item for item in self.schema.source_mappings
                if item.domain is source_ref.domain and item.source_type == source_ref.source_type
            ),
            None,
        )
        if mapping is None or mapping.entity_type != entity_type:
            raise ValueError("source reference does not match the graph schema mapping")

    @staticmethod
    def _entity_record(row: dict[str, Any]) -> GraphEntityRecord:
        source_ref = None
        if row["source_domain"] is not None:
            source_ref = GraphSourceReference(
                domain=GraphSourceDomain(row["source_domain"]),
                source_type=row["source_type"],
                source_id=row["source_id"],
                source_version=row["source_version"],
            )
        entity = GraphEntity(
            tenant_id=row["tenant_id"],
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            attributes=dict(row["attributes"] or {}),
            project_id=row["project_id"],
        )
        return GraphEntityRecord(
            entity=entity, name=row["entity_name"], source_ref=source_ref,
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _relation_record(row: dict[str, Any]) -> GraphRelationRecord:
        relation = GraphRelation(
            tenant_id=row["tenant_id"],
            relation_id=row["relation_id"],
            relation_type=row["relation_type"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            source_entity_type=row["source_entity_type"],
            target_entity_type=row["target_entity_type"],
            attributes=dict(row["attributes"] or {}),
            project_id=row["project_id"],
        )
        return GraphRelationRecord(
            relation=relation, created_at=row["created_at"], updated_at=row["updated_at"]
        )

    async def upsert_entity(
        self,
        entity: GraphEntity,
        *,
        name: str,
        source_ref: GraphSourceReference | None = None,
        allowed_project_ids: Sequence[str] = (),
    ) -> GraphEntityRecord:
        entity.validate(self.schema)
        if not isinstance(name, str) or not name.strip() or len(name) > 500:
            raise ValueError("entity name must contain 1-500 non-whitespace characters")
        self._check_project_write(entity.project_id, allowed_project_ids)
        self._check_source_ref(entity.entity_type, source_ref)
        source = source_ref
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await self._lock_entity_ids(connection, (entity.entity_id,))
                cursor = await connection.execute(
                    """
                    SELECT 1 FROM graph_entity_tombstones
                    WHERE tenant_id=%s AND entity_id=%s
                    """,
                    (self.tenant_id, entity.entity_id),
                )
                if await cursor.fetchone() is not None:
                    raise ValueError("graph entity was deleted and cannot be resurrected")
                cursor = await connection.execute(
                    """
                    INSERT INTO graph_entities (
                        tenant_id, entity_id, entity_type, entity_name, attributes,
                        project_id, source_domain, source_type, source_id, source_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, entity_id) DO UPDATE SET
                        entity_type=EXCLUDED.entity_type,
                        entity_name=EXCLUDED.entity_name,
                        attributes=EXCLUDED.attributes,
                        project_id=EXCLUDED.project_id,
                        source_domain=EXCLUDED.source_domain,
                        source_type=EXCLUDED.source_type,
                        source_id=EXCLUDED.source_id,
                        source_version=EXCLUDED.source_version,
                        updated_at=now()
                    WHERE graph_entities.project_id IS NOT DISTINCT FROM EXCLUDED.project_id
                       OR NOT EXISTS (
                           SELECT 1 FROM graph_relations
                           WHERE tenant_id=EXCLUDED.tenant_id
                             AND (source_entity_id=EXCLUDED.entity_id
                                  OR target_entity_id=EXCLUDED.entity_id)
                       )
                    RETURNING *
                    """,
                    (
                        self.tenant_id, entity.entity_id, entity.entity_type, name.strip(),
                        _jsonb(dict(entity.attributes)), entity.project_id,
                        source.domain.value if source else None,
                        source.source_type if source else None,
                        source.source_id if source else None,
                        source.source_version if source else None,
                    ),
                )
                row = await cursor.fetchone()
                if row is None:
                    raise ValueError("cannot move an entity across project scope while relations exist")
        return self._entity_record(row)

    async def get_entity(
        self, entity_id: str, *, allowed_project_ids: Sequence[str] = ()
    ) -> GraphEntityRecord | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """
                SELECT * FROM graph_entities
                WHERE tenant_id=%s AND entity_id=%s
                  AND (project_id IS NULL OR project_id=ANY(%s))
                """,
                (self.tenant_id, entity_id, list(allowed_project_ids)),
            )
            row = await cursor.fetchone()
        return self._entity_record(row) if row else None

    async def search_entities(
        self,
        query: str,
        *,
        entity_type: str | None = None,
        allowed_project_ids: Sequence[str] = (),
        limit: int = 50,
    ) -> list[GraphEntityRecord]:
        if not isinstance(query, str) or not query.strip() or len(query) > 200:
            raise ValueError("query must contain 1-200 non-whitespace characters")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if entity_type is not None:
            self.schema.entity_type(entity_type)
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """
                SELECT * FROM graph_entities
                WHERE tenant_id=%s
                  AND (project_id IS NULL OR project_id=ANY(%s))
                  AND lower(entity_name) LIKE lower(%s) || '%%'
                  AND (%s::text IS NULL OR entity_type=%s)
                ORDER BY lower(entity_name), entity_id
                LIMIT %s
                """,
                (
                    self.tenant_id, list(allowed_project_ids), query.strip(),
                    entity_type, entity_type, limit,
                ),
            )
            rows = await cursor.fetchall()
        return [self._entity_record(row) for row in rows]

    async def upsert_relation(
        self,
        relation: GraphRelation,
        *,
        allowed_project_ids: Sequence[str] = (),
    ) -> GraphRelationRecord:
        relation.validate(self.schema)
        self._check_project_write(relation.project_id, allowed_project_ids)
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await self._lock_entity_ids(
                    connection, (relation.source_entity_id, relation.target_entity_id)
                )
                cursor = await connection.execute(
                    """
                    SELECT entity_id, entity_type, project_id
                    FROM graph_entities
                    WHERE tenant_id=%s AND entity_id=ANY(%s)
                    FOR KEY SHARE
                    """,
                    (
                        self.tenant_id,
                        [relation.source_entity_id, relation.target_entity_id],
                    ),
                )
                endpoints = {row["entity_id"]: row for row in await cursor.fetchall()}
                source_endpoint = endpoints.get(relation.source_entity_id)
                target_endpoint = endpoints.get(relation.target_entity_id)
                if source_endpoint is None or target_endpoint is None:
                    raise ValueError("both relation endpoints must exist in the same tenant")
                if source_endpoint["entity_type"] != relation.source_entity_type:
                    raise ValueError("source endpoint type does not match stored entity")
                if target_endpoint["entity_type"] != relation.target_entity_type:
                    raise ValueError("target endpoint type does not match stored entity")
                for endpoint in (source_endpoint, target_endpoint):
                    if endpoint["project_id"] not in (None, relation.project_id):
                        raise PermissionError("relation crosses project scope")
                cursor = await connection.execute(
                    """
                    INSERT INTO graph_relations (
                        tenant_id, relation_id, relation_type, source_entity_id,
                        target_entity_id, source_entity_type, target_entity_type,
                        attributes, project_id
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (tenant_id, relation_id) DO UPDATE SET
                        relation_type=EXCLUDED.relation_type,
                        source_entity_id=EXCLUDED.source_entity_id,
                        target_entity_id=EXCLUDED.target_entity_id,
                        source_entity_type=EXCLUDED.source_entity_type,
                        target_entity_type=EXCLUDED.target_entity_type,
                        attributes=EXCLUDED.attributes,
                        project_id=EXCLUDED.project_id,
                        updated_at=now()
                    RETURNING *
                    """,
                    (
                        self.tenant_id, relation.relation_id, relation.relation_type,
                        relation.source_entity_id, relation.target_entity_id,
                        relation.source_entity_type, relation.target_entity_type,
                        _jsonb(dict(relation.attributes)), relation.project_id,
                    ),
                )
                row = await cursor.fetchone()
        return self._relation_record(row)

    async def list_relations(
        self,
        entity_id: str,
        *,
        direction: str = "both",
        allowed_project_ids: Sequence[str] = (),
        limit: int = 200,
    ) -> list[GraphRelationRecord]:
        if direction not in {"in", "out", "both"}:
            raise ValueError("direction must be in, out or both")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        predicate = (
            "source_entity_id=%s" if direction == "out"
            else "target_entity_id=%s" if direction == "in"
            else "(source_entity_id=%s OR target_entity_id=%s)"
        )
        params: list[Any] = [self.tenant_id]
        params.extend([entity_id] if direction != "both" else [entity_id, entity_id])
        params.extend([list(allowed_project_ids), limit])
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                f"""
                SELECT * FROM graph_relations
                WHERE tenant_id=%s AND {predicate}
                  AND (project_id IS NULL OR project_id=ANY(%s))
                ORDER BY created_at, relation_id LIMIT %s
                """,
                tuple(params),
            )
            rows = await cursor.fetchall()
        return [self._relation_record(row) for row in rows]

    async def delete_entity(self, entity_id: str, *, allowed_project_ids: Sequence[str] = ()) -> bool:
        """Delete one entity atomically and enqueue durable derived-data invalidations.

        The transaction-scoped advisory lock is shared with entity/relation writes.
        Therefore a relation writer either commits before the relation snapshot below
        (and is included in the outbox) or observes the missing endpoint after delete.
        """
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError("entity_id is required")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await self._lock_entity_ids(connection, (entity_id,))
                cursor = await connection.execute(
                    """
                    SELECT entity_id FROM graph_entities
                    WHERE tenant_id=%s AND entity_id=%s
                      AND (project_id IS NULL OR project_id=ANY(%s))
                    FOR UPDATE
                    """,
                    (self.tenant_id, entity_id, list(allowed_project_ids)),
                )
                if await cursor.fetchone() is None:
                    return False
                cursor = await connection.execute(
                    """
                    SELECT relation_id FROM graph_relations
                    WHERE tenant_id=%s
                      AND (source_entity_id=%s OR target_entity_id=%s)
                    ORDER BY relation_id
                    FOR UPDATE
                    """,
                    (self.tenant_id, entity_id, entity_id),
                )
                relation_ids = [row["relation_id"] for row in await cursor.fetchall()]
                cursor = await connection.execute(
                    """
                    DELETE FROM graph_entities
                    WHERE tenant_id=%s AND entity_id=%s
                    RETURNING entity_id
                    """,
                    (self.tenant_id, entity_id),
                )
                if await cursor.fetchone() is None:
                    return False
                await connection.execute(
                    """
                    INSERT INTO graph_entity_tombstones (tenant_id, entity_id)
                    VALUES (%s, %s)
                    ON CONFLICT (tenant_id, entity_id) DO NOTHING
                    """,
                    (self.tenant_id, entity_id),
                )
                invalidations = [
                    ("entity", entity_id, "delete"),
                    ("cache", entity_id, "invalidate"),
                    *(("relation", relation_id, "delete") for relation_id in relation_ids),
                ]
                for object_kind, object_id, operation in invalidations:
                    await connection.execute(
                        """
                        INSERT INTO graph_invalidation_outbox (
                            tenant_id, object_kind, object_id, operation, idempotency_key
                        ) VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (tenant_id, idempotency_key) DO NOTHING
                        """,
                        (
                            self.tenant_id, object_kind, object_id, operation,
                            f"delete:{entity_id}:{object_kind}:{object_id}",
                        ),
                    )
                return True

    async def claim_invalidation_jobs(
        self, *, worker_id: str, limit: int = 50, lease_seconds: int = 60,
    ) -> list[dict[str, Any]]:
        if not isinstance(worker_id, str) or not worker_id.strip():
            raise ValueError("worker_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if isinstance(lease_seconds, bool) or not isinstance(lease_seconds, int) or not 5 <= lease_seconds <= 3600:
            raise ValueError("lease_seconds must be between 5 and 3600")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    WITH candidates AS (
                        SELECT job_id FROM graph_invalidation_outbox
                        WHERE tenant_id=%s
                          AND ((status='pending' AND available_at<=now())
                            OR (status='processing' AND lease_until<=now()))
                          AND attempts < 8
                        ORDER BY available_at, job_id
                        FOR UPDATE SKIP LOCKED
                        LIMIT %s
                    )
                    UPDATE graph_invalidation_outbox AS job
                    SET status='processing', attempts=job.attempts+1,
                        lease_owner=%s, lease_until=now() + (%s * interval '1 second')
                    FROM candidates
                    WHERE job.job_id=candidates.job_id
                    RETURNING job.*
                    """,
                    (self.tenant_id, limit, worker_id, lease_seconds),
                )
                return list(await cursor.fetchall())

    async def complete_invalidation_job(self, *, job_id: str, worker_id: str) -> bool:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """
                UPDATE graph_invalidation_outbox
                SET status='completed', lease_owner=NULL, lease_until=NULL,
                    completed_at=now()
                WHERE tenant_id=%s AND job_id=%s AND status='processing'
                  AND lease_owner=%s AND lease_until>now()
                RETURNING job_id
                """,
                (self.tenant_id, job_id, worker_id),
            )
            return await cursor.fetchone() is not None
