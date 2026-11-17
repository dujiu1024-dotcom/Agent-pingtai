"""Tenant-scoped knowledge graph schema contracts."""

from agent_platform.graph.query import (
    GraphHybridResult,
    GraphQueryAuthorizationError,
    GraphQueryError,
    GraphQueryMode,
    GraphQueryResult,
    GraphQueryService,
    GraphQuerySpec,
    GraphQueryTimeout,
)
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
    "GraphHybridResult",
    "GraphQueryAuthorizationError",
    "GraphQueryError",
    "GraphQueryMode",
    "GraphQueryResult",
    "GraphQueryService",
    "GraphQuerySpec",
    "GraphQueryTimeout",
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
