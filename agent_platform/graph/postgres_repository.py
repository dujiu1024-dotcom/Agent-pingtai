"""Tenant-bound PostgreSQL graph record repository.

The canonical Knowledge, Memory and Blackboard records remain their own
systems of record; graph rows contain indexed references and derived attributes.
"""
from __future__ import annotations

from typing import Any, Callable

from psycopg.types.json import Jsonb

from agent_platform.graph.schema import GraphEntity, GraphRelation, GraphSchema

ConnectionFactory = Callable[[], Any]


class PostgresGraphRepository:
    def __init__(
        self, *, tenant_id: str, schema: GraphSchema,
        connection_factory: ConnectionFactory,
    ) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if schema.tenant_id != tenant_id:
            raise ValueError("graph schema tenant does not match repository tenant")
        self.tenant_id = tenant_id
        self.schema = schema
        self._connection_factory = connection_factory

    @classmethod
    def from_dsn(
        cls, *, tenant_id: str, schema: GraphSchema, dsn: str,
    ) -> "PostgresGraphRepository":
        if not isinstance(dsn, str) or not dsn.strip():
            raise ValueError("PostgreSQL DSN is required")

        async def connect() -> Any:
            try:
                from psycopg import AsyncConnection
                from psycopg.rows import dict_row
                from psycopg.types.json import Jsonb as PsycopgJsonb
            except ImportError as exc:
                raise RuntimeError(
                    "Install psycopg[binary,pool] for PostgreSQL storage"
                ) from exc
            # Resolve Jsonb lazily in _jsonb; importing it here validates the extra.
            del PsycopgJsonb
            return await AsyncConnection.connect(dsn, row_factory=dict_row)

        return cls(tenant_id=tenant_id, schema=schema, connection_factory=connect)

    @staticmethod
    def _jsonb(value: Any) -> Any:
        try:
            from psycopg.types.json import Jsonb as PsycopgJsonb
        except ImportError as exc:
            raise RuntimeError(
                "Install psycopg[binary,pool] for PostgreSQL storage"
            ) from exc
        return PsycopgJsonb(value)

    def _entity_row(self, row: dict[str, Any]) -> GraphEntity:
        return GraphEntity(
            tenant_id=self.tenant_id,
            entity_id=row["entity_id"],
            entity_type=row["entity_type"],
            project_id=row["project_id"],
            attributes=dict(row["attributes"] or {}),
        )

    def _relation_row(self, row: dict[str, Any]) -> GraphRelation:
        relation_type = self.schema.relation_type(row["relation_type"])
        return GraphRelation(
            tenant_id=self.tenant_id,
            relation_id=row["relation_id"],
            relation_type=row["relation_type"],
            source_entity_id=row["source_entity_id"],
            target_entity_id=row["target_entity_id"],
            source_entity_type=row["source_entity_type"],
            target_entity_type=row["target_entity_type"],
            project_id=row["project_id"],
            attributes=dict(row["attributes"] or {}),
        )

    async def put_entity(self, entity: GraphEntity) -> GraphEntity:
        entity.validate(self.schema)
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_graph_entities
                   (tenant_id, entity_id, entity_type, project_id, attributes)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (tenant_id, entity_id) DO UPDATE SET
                     entity_type = EXCLUDED.entity_type,
                     project_id = EXCLUDED.project_id,
                     attributes = EXCLUDED.attributes,
                     updated_at = now()
                   WHERE platform_graph_entities.entity_type = EXCLUDED.entity_type
                   RETURNING entity_id, entity_type, project_id, attributes""",
                (self.tenant_id, entity.entity_id, entity.entity_type,
                 entity.project_id, self._jsonb(dict(entity.attributes))),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ValueError("graph entity ID is already bound to a different entity type")
        return self._entity_row(row)

    async def get_entity(self, entity_id: str) -> GraphEntity | None:
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError("entity_id must not be empty")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT entity_id, entity_type, project_id, attributes
                   FROM platform_graph_entities
                   WHERE tenant_id = %s AND entity_id = %s""",
                (self.tenant_id, entity_id),
            )
            row = await cursor.fetchone()
        return self._entity_row(row) if row else None

    async def put_relation(self, relation: GraphRelation) -> GraphRelation:
        relation.validate(self.schema)
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """INSERT INTO platform_graph_relations
                   (tenant_id, relation_id, relation_type, source_entity_id,
                    target_entity_id, project_id, attributes)
                   SELECT %s, %s, %s, source.entity_id, target.entity_id, %s, %s
                   FROM platform_graph_entities AS source
                   JOIN platform_graph_entities AS target
                     ON target.tenant_id = source.tenant_id
                   WHERE source.tenant_id = %s AND source.entity_id = %s
                     AND target.entity_id = %s
                     AND source.entity_type = %s AND target.entity_type = %s
                   ON CONFLICT (tenant_id, relation_id) DO NOTHING
                   RETURNING relation_id""",
                (self.tenant_id, relation.relation_id, relation.relation_type,
                 relation.project_id, self._jsonb(dict(relation.attributes)),
                 self.tenant_id, relation.source_entity_id, relation.target_entity_id,
                 relation.source_entity_type, relation.target_entity_type),
            )
            row = await cursor.fetchone()
        if row is None:
            raise ValueError("graph relation endpoints missing or do not match schema")
        return relation

    async def list_relations(
        self, entity_id: str, *, limit: int = 100,
    ) -> tuple[GraphRelation, ...]:
        if not isinstance(entity_id, str) or not entity_id.strip():
            raise ValueError("entity_id must not be empty")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("limit must be an integer between 1 and 1000")
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT relation.relation_id, relation.relation_type,
                          relation.source_entity_id, relation.target_entity_id,
                          relation.project_id, relation.attributes,
                          source.entity_type AS source_entity_type,
                          target.entity_type AS target_entity_type
                   FROM platform_graph_relations AS relation
                   JOIN platform_graph_entities AS source
                     ON source.tenant_id = relation.tenant_id
                    AND source.entity_id = relation.source_entity_id
                   JOIN platform_graph_entities AS target
                     ON target.tenant_id = relation.tenant_id
                    AND target.entity_id = relation.target_entity_id
                   WHERE relation.tenant_id = %s
                     AND (relation.source_entity_id = %s OR relation.target_entity_id = %s)
                   ORDER BY relation.created_at, relation.relation_id
                   LIMIT %s""",
                (self.tenant_id, entity_id, entity_id, limit),
            )
            rows = await cursor.fetchall()
        return tuple(self._relation_row(row) for row in rows)
