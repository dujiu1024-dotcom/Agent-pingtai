from __future__ import annotations

import math
import unittest

from agent_platform.graph.schema import (
    EntityTypeDefinition,
    GraphEntity,
    GraphRelation,
    GraphSchemaRegistry,
    GraphSourceDomain,
    PropertyDefinition,
    PropertyKind,
    RelationTypeDefinition,
    SourceTypeMapping,
    default_graph_schema,
)


class GraphSchemaTests(unittest.TestCase):
    def test_default_schema_maps_canonical_platform_domains(self):
        schema = default_graph_schema("tenant-a")
        self.assertEqual(
            {(item.domain, item.source_type, item.entity_type) for item in schema.source_mappings},
            {
                (GraphSourceDomain.KNOWLEDGE, "document", "document"),
                (GraphSourceDomain.MEMORY, "memory", "memory"),
                (GraphSourceDomain.BLACKBOARD, "entry", "blackboard_entry"),
                (GraphSourceDomain.FILE, "parsed_file", "document"),
            },
        )

    def test_entity_and_relation_attributes_enforce_required_type_and_range(self):
        registry = GraphSchemaRegistry()
        registry.register_entity_type(
            "tenant-a",
            EntityTypeDefinition(
                "person",
                (
                    PropertyDefinition("name", PropertyKind.STRING, required=True, max_length=80),
                    PropertyDefinition("age", PropertyKind.INTEGER, minimum=0, maximum=150),
                ),
            ),
        )
        schema = registry.get("tenant-a")
        GraphEntity("tenant-a", "person-1", "person", {"name": "Ada", "age": 36}).validate(schema)
        with self.assertRaisesRegex(ValueError, "required property"):
            GraphEntity("tenant-a", "person-2", "person", {}).validate(schema)
        with self.assertRaisesRegex(ValueError, "must be integer"):
            GraphEntity("tenant-a", "person-3", "person", {"name": "Ada", "age": True}).validate(schema)
        with self.assertRaisesRegex(ValueError, "maximum"):
            GraphEntity("tenant-a", "person-4", "person", {"name": "Ada", "age": 151}).validate(schema)
        with self.assertRaisesRegex(ValueError, "finite number"):
            PropertyDefinition("score", PropertyKind.NUMBER, minimum=math.nan)
        registry = GraphSchemaRegistry()
        schema = registry.register_entity_type(
            "tenant-a",
            EntityTypeDefinition(
                "measurement",
                (PropertyDefinition("score", PropertyKind.NUMBER),),
            ),
        )
        with self.assertRaisesRegex(ValueError, "must be number"):
            GraphEntity(
                "tenant-a", "measurement-1", "measurement", {"score": math.inf}
            ).validate(schema)

    def test_relation_endpoint_types_are_constrained(self):
        registry = GraphSchemaRegistry()
        registry.register_entity_type("tenant-a", EntityTypeDefinition("person"))
        registry.register_entity_type("tenant-a", EntityTypeDefinition("company"))
        registry.register_relation_type(
            "tenant-a", RelationTypeDefinition("works_for", ("person",), ("company",))
        )
        relation = GraphRelation(
            "tenant-a", "r1", "works_for", "p1", "c1", "person", "company"
        )
        relation.validate(registry.get("tenant-a"))
        invalid = GraphRelation(
            "tenant-a", "r2", "works_for", "c1", "p1", "company", "person"
        )
        with self.assertRaisesRegex(ValueError, "source entity type"):
            invalid.validate(registry.get("tenant-a"))

    def test_runtime_extensions_are_tenant_isolated_and_cannot_override_builtins(self):
        registry = GraphSchemaRegistry()
        registry.register_entity_type("tenant-a", EntityTypeDefinition("invoice"))
        self.assertNotIn("invoice", {item.name for item in registry.get("tenant-b").entity_types})
        with self.assertRaisesRegex(ValueError, "cannot be overridden"):
            registry.register_entity_type("tenant-a", EntityTypeDefinition("document"))
        with self.assertRaisesRegex(ValueError, "already exists"):
            registry.register_entity_type("tenant-a", EntityTypeDefinition("invoice"))

    def test_schema_rejects_cross_tenant_records_and_unknown_properties(self):
        schema = default_graph_schema("tenant-a")
        with self.assertRaisesRegex(ValueError, "tenant does not match"):
            GraphEntity("tenant-b", "doc-1", "document", {"title": "x"}).validate(schema)
        with self.assertRaisesRegex(ValueError, "unknown properties"):
            GraphEntity("tenant-a", "doc-2", "document", {"title": "x", "secret": "x"}).validate(schema)

    def test_invalid_definitions_and_source_mapping_duplicates_are_rejected(self):
        with self.assertRaises(ValueError):
            EntityTypeDefinition("Bad Name")
        with self.assertRaises(ValueError):
            PropertyDefinition("age", PropertyKind.INTEGER, minimum=10, maximum=1)
        with self.assertRaises(ValueError):
            from agent_platform.graph.schema import GraphSchema
            GraphSchema(
                "tenant-a",
                default_graph_schema("tenant-a").entity_types,
                default_graph_schema("tenant-a").relation_types,
                (
                    SourceTypeMapping(GraphSourceDomain.KNOWLEDGE, "document", "document"),
                    SourceTypeMapping(GraphSourceDomain.KNOWLEDGE, "document", "document"),
                ),
            )


if __name__ == "__main__":
    unittest.main()
