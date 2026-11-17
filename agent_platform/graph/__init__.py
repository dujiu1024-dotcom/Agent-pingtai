"""Tenant-scoped knowledge graph schema contracts."""

from agent_platform.graph.schema import (
    EntityTypeDefinition,
    GraphEntity,
    GraphRelation,
    GraphSchema,
    GraphSchemaRegistry,
    GraphSourceDomain,
    PropertyDefinition,
    PropertyKind,
    RelationTypeDefinition,
    SourceTypeMapping,
    default_graph_schema,
)
from agent_platform.graph.postgres_repository import (
    GraphEntityRecord,
    GraphRelationRecord,
    GraphSourceReference,
    PostgresGraphRepository,
)

__all__ = [
    "GraphEntityRecord",
    "GraphRelationRecord",
    "GraphSourceReference",
    "PostgresGraphRepository",
    "EntityTypeDefinition",
    "GraphEntity",
    "GraphRelation",
    "GraphSchema",
    "GraphSchemaRegistry",
    "GraphSourceDomain",
    "PropertyDefinition",
    "PropertyKind",
    "RelationTypeDefinition",
    "SourceTypeMapping",
    "default_graph_schema",
]
