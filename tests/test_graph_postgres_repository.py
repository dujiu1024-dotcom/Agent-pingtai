from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

from agent_platform.graph.postgres_repository import PostgresGraphRepository
from agent_platform.graph.schema import (
    EntityTypeDefinition,
    GraphEntity,
    GraphRelation,
    GraphSchemaRegistry,
    PropertyDefinition,
    PropertyKind,
    RelationTypeDefinition,
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

    def transaction(self):
        return self

    async def execute(self, query, params=()):
        # 关键 SQL：entity/relation 写入和读取
        if "INSERT INTO graph_entities" in query or "INSERT INTO platform_graph_entities" in query:
            # 字段顺序: tenant_id, entity_id, entity_type, entity_name, attributes, project_id, source_*
            tenant = params[0]; entity_id = params[1]; entity_type = params[2]
            name = params[3] if len(params) > 3 else None
            attributes = params[4].obj if len(params) > 4 and hasattr(params[4], 'obj') else (params[4] if len(params) > 4 else {})
            project_id = params[5] if len(params) > 5 else None
            if tenant != self.tenant:
                return _Cursor()
            key = (tenant, entity_id)
            current = self.entities.get(key)
            if current and current["entity_type"] != entity_type:
                return _Cursor()
            row = {"tenant_id": tenant, "entity_id": entity_id, "entity_type": entity_type,
                   "source_domain": None, "source_type": None, "source_id": None,
                   "source_version": None, "entity_name": name, "attributes": attributes,
                   "project_id": project_id, "created_at": 0, "updated_at": 0}
            self.entities[key] = row
            return _Cursor(row)
        if "FROM graph_entities" in query or "SELECT entity_id, entity_type" in query:
            tenant = str(params[0])
            # 支持 entity_id=ANY(%s) 查询（params[1] 是 list）或简单 =查询
            entity_ids = params[1] if isinstance(params[1], (list, tuple)) else [params[1]]
            rows = []
            for eid in entity_ids:
                row = self.entities.get((tenant, str(eid))) if tenant == self.tenant else None
                if row:
                    rows.append(row)
            if len(rows) == 1:
                return _Cursor(row=rows[0], rows=rows)
            if rows:
                return _Cursor(row=rows[0], rows=rows)
            # 空结果
            row = self.entities.get((tenant, str(params[1]))) if tenant == self.tenant else None
            return _Cursor(row=row)
        if "INSERT INTO graph_relations" in query or "INSERT INTO platform_graph_relations" in query:
            # params: tenant_id=0, relation_id=1, relation_type=2, source_entity_id=3,
            #         target_entity_id=4, source_entity_type=5, target_entity_type=6,
            #         attributes=7, project_id=8
            tenant = str(params[0])
            relation_id = str(params[1])
            relation_type = str(params[2])
            source_id = str(params[3])
            target_id = str(params[4])
            source_type = str(params[5])
            target_type = str(params[6])
            attributes = params[7].obj if hasattr(params[7], 'obj') else (params[7] if len(params) > 7 else {})
            project_id = params[8] if len(params) > 8 else None
            source = self.entities.get((tenant, source_id))
            target = self.entities.get((tenant, target_id))
            if not source or not target:
                return _Cursor()
            row = {"tenant_id": tenant, "relation_id": relation_id,
                   "relation_type": relation_type, "source_entity_id": source_id,
                   "target_entity_id": target_id, "source_entity_type": source_type,
                   "target_entity_type": target_type, "attributes": attributes,
                   "project_id": project_id, "created_at": 0, "updated_at": 0}
            self.relations[("tenant-a", relation_id)] = row
            return _Cursor(row)
        if "FROM graph_relations" in query or "FROM platform_graph_relations" in query:
            tenant = str(params[0])
            entity_id = str(params[1])
            rows = []
            for (rt, rid), row in self.relations.items():
                if rt == tenant and (row.get("source_entity_id") == entity_id or row.get("target_entity_id") == entity_id):
                    rows.append(row)
            return _Cursor(rows=rows) if rows else _Cursor()
        # advisory lock, tombstone check, 其他辅助 SQL 一律放行
        return _Cursor()


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
        stored = await self.repository.upsert_entity(entity, name="Ada Lovelace", allowed_project_ids=("project-1",))
        self.assertEqual(stored.entity, entity)
        fetched = await self.repository.get_entity("p-1")
        self.assertEqual(fetched.entity, entity)
        self.assertIsNone(await self.repository.get_entity("not-found"))

    async def test_rejects_schema_tenant_mismatch_and_invalid_attributes(self):
        with self.assertRaisesRegex(ValueError, "schema tenant"):
            PostgresGraphRepository(
                tenant_id="tenant-b", schema=self.schema, connection_factory=lambda: None,
            )
        invalid = GraphEntity("tenant-a", "p-2", "person", {"unknown": "value"})
        with self.assertRaisesRegex(ValueError, "unknown properties"):
            await self.repository.upsert_entity(invalid, name="bad")
        self.assertEqual(self.entities, {})

    async def test_relation_requires_persisted_endpoints_and_lists_adjacency(self):
        await self.repository.upsert_entity(
            GraphEntity("tenant-a", "p-1", "person", {"name": "Ada"}), name="Ada"
        )
        await self.repository.upsert_entity(
            GraphEntity("tenant-a", "c-1", "company", {"name": "Analytical"}), name="Analytical"
        )
        relation = GraphRelation(
            "tenant-a", "r-1", "works_for", "p-1", "c-1", "person", "company",
            {},
        )
        stored = await self.repository.upsert_relation(relation)
        self.assertEqual(stored.relation, relation)
        listed = await self.repository.list_relations("p-1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].relation, relation)
        with self.assertRaisesRegex(ValueError, "limit"):
            await self.repository.list_relations("p-1", limit=True)

    async def test_cross_tenant_record_is_rejected_before_query(self):
        with self.assertRaisesRegex(ValueError, "tenant does not match"):
            await self.repository.upsert_entity(
                GraphEntity("tenant-b", "p-1", "person", {"name": "Ada"}), name="Ada"
            )


if __name__ == "__main__":
    unittest.main()
