from __future__ import annotations

import sys
import types
import unittest
from contextlib import asynccontextmanager
from unittest.mock import patch

from agent_platform.graph.postgres_repository import PostgresGraphRepository
from agent_platform.graph.schema import (
    EntityTypeDefinition,
    GraphEntity,
    GraphRelation,
    GraphSchemaRegistry,
    RelationTypeDefinition,
    PropertyDefinition,
    PropertyKind,
)


class _Jsonb:
    def __init__(self, obj):
        self.obj = obj


class _Cursor:
    def __init__(self, row=None, rows=None):
        self.row = row
        self.rows = rows or []

    async def fetchone(self):
        return self.row

    async def fetchall(self):
        return self.rows


class _Connection:
    def __init__(self, entities, relations, tenant):
        self.entities = entities
        self.relations = relations
        self.tenant = tenant

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, query, params=()):
        if "INSERT INTO platform_graph_entities" in query:
            tenant, entity_id, entity_type, project_id, attributes = params
            if tenant != self.tenant:
                return _Cursor()
            key = (tenant, entity_id)
            current = self.entities.get(key)
            if current and current["entity_type"] != entity_type:
                return _Cursor()
            row = {
                "entity_id": entity_id, "entity_type": entity_type,
                "project_id": project_id, "attributes": attributes.obj,
            }
            self.entities[key] = row
            return _Cursor(row)
        if "SELECT entity_id, entity_type, project_id, attributes" in query:
            tenant, entity_id = params
            row = self.entities.get((tenant, entity_id)) if tenant == self.tenant else None
            return _Cursor(row)
        if "INSERT INTO platform_graph_relations" in query:
            (tenant, relation_id, relation_type, project_id, attributes,
             query_tenant, source_id, target_id, source_type, target_type) = params
            source = self.entities.get((tenant, source_id))
            target = self.entities.get((tenant, target_id))
            if (tenant != self.tenant or query_tenant != self.tenant or not source or not target
                or source["entity_type"] != source_type or target["entity_type"] != target_type):
                return _Cursor()
            key = (tenant, relation_id)
            if key in self.relations:
                return _Cursor()
            self.relations[key] = {
                "relation_id": relation_id, "relation_type": relation_type,
                "source_entity_id": source_id, "target_entity_id": target_id,
                "project_id": project_id, "attributes": attributes.obj,
                "source_entity_type": source_type, "target_entity_type": target_type,
            }
            return _Cursor({"relation_id": relation_id})
        if "FROM platform_graph_relations AS relation" in query:
            tenant, entity_id, other_id, _limit = params
            rows = []
            if tenant == self.tenant:
                rows = [
                    row for (row_tenant, _), row in self.relations.items()
                    if row_tenant == tenant and entity_id in (
                        row["source_entity_id"], row["target_entity_id"]
                    )
                ][: _limit]
            return _Cursor(rows=rows)
        raise AssertionError(f"unexpected SQL: {query}")


class PostgresGraphRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entities = {}
        self.relations = {}
        async def connect():
            return _Connection(self.entities, self.relations, "tenant-a")
        registry = GraphSchemaRegistry()
        registry.register_entity_type("tenant-a", EntityTypeDefinition(
            "person", (PropertyDefinition("name", PropertyKind.STRING),)
        ))
        registry.register_entity_type("tenant-a", EntityTypeDefinition(
            "company", (PropertyDefinition("name", PropertyKind.STRING),)
        ))
        registry.register_relation_type(
            "tenant-a", RelationTypeDefinition("works_for", ("person",), ("company",))
        )
        self.schema = registry.get("tenant-a")
        self.repository = PostgresGraphRepository(
            tenant_id="tenant-a", schema=self.schema, connection_factory=connect,
        )
        psycopg = types.ModuleType("psycopg")
        psycopg.__path__ = []
        psycopg_types = types.ModuleType("psycopg.types")
        psycopg_types.__path__ = []
        psycopg_json = types.ModuleType("psycopg.types.json")
        psycopg_json.Jsonb = _Jsonb
        self.modules = patch.dict(sys.modules, {
            "psycopg": psycopg, "psycopg.types": psycopg_types,
            "psycopg.types.json": psycopg_json,
        })
        self.modules.start()
        self.addCleanup(self.modules.stop)

    async def test_tenant_scoped_entity_round_trip(self):
        entity = GraphEntity("tenant-a", "p-1", "person", {"name": "Ada"}, "project-1")
        stored = await self.repository.put_entity(entity)
        self.assertEqual(stored, entity)
        self.assertEqual(await self.repository.get_entity("p-1"), entity)
        self.assertIsNone(await self.repository.get_entity("not-found"))

    async def test_rejects_schema_tenant_mismatch_and_invalid_attributes(self):
        with self.assertRaisesRegex(ValueError, "schema tenant"):
            PostgresGraphRepository(
                tenant_id="tenant-b", schema=self.schema, connection_factory=lambda: None,
            )
        invalid = GraphEntity("tenant-a", "p-2", "person", {"unknown": "value"})
        with self.assertRaisesRegex(ValueError, "unknown properties"):
            await self.repository.put_entity(invalid)
        self.assertEqual(self.entities, {})

    async def test_relation_requires_persisted_endpoints_and_lists_adjacency(self):
        await self.repository.put_entity(
            GraphEntity("tenant-a", "p-1", "person", {"name": "Ada"})
        )
        await self.repository.put_entity(
            GraphEntity("tenant-a", "c-1", "company", {"name": "Analytical"})
        )
        relation = GraphRelation(
            "tenant-a", "r-1", "works_for", "p-1", "c-1", "person", "company",
            {"since": 2024},
        )
        self.assertEqual(await self.repository.put_relation(relation), relation)
        self.assertEqual(await self.repository.list_relations("p-1"), (relation,))
        with self.assertRaisesRegex(ValueError, "limit"):
            await self.repository.list_relations("p-1", limit=True)

    async def test_cross_tenant_record_is_rejected_before_query(self):
        with self.assertRaisesRegex(ValueError, "tenant does not match"):
            await self.repository.put_entity(
                GraphEntity("tenant-b", "p-1", "person", {"name": "Ada"})
            )


if __name__ == "__main__":
    unittest.main()
