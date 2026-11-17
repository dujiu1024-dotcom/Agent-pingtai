"""Tenant-scoped knowledge graph schema contracts."""

from agent_platform.graph.postgres_repository import PostgresGraphRepository
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

__all__ = [
    "EntityTypeDefinition",
    "GraphEntity",
    "GraphRelation",
    "GraphSchema",
    "GraphSchemaRegistry",
    "GraphSourceDomain",
    "PostgresGraphRepository",
    "PropertyDefinition",
    "PropertyKind",
    "RelationTypeDefinition",
    "SourceTypeMapping",
    "default_graph_schema",
]
