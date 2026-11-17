"""Tenant-isolated, extensible knowledge graph schema contracts.

This module defines graph metadata only. It deliberately does not select or
implement a graph database; persistence and query execution belong to later tasks.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from threading import RLock
from typing import Any, Mapping


_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
_BUILTIN_TYPES = frozenset({"document", "memory", "blackboard_entry", "user", "project"})


class PropertyKind(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    STRING_LIST = "string_list"


class GraphSourceDomain(str, Enum):
    KNOWLEDGE = "knowledge"
    MEMORY = "memory"
    BLACKBOARD = "blackboard"


@dataclass(frozen=True)
class PropertyDefinition:
    name: str
    kind: PropertyKind
    required: bool = False
    indexed: bool = False
    max_length: int | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    allowed_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "property name")
        if not isinstance(self.kind, PropertyKind):
            raise ValueError("kind must be a PropertyKind")
        if not isinstance(self.required, bool) or not isinstance(self.indexed, bool):
            raise ValueError("required and indexed must be booleans")
        if self.max_length is not None and (
            isinstance(self.max_length, bool)
            or not isinstance(self.max_length, int)
            or self.max_length < 1
        ):
            raise ValueError("max_length must be a positive integer")
        for name in ("minimum", "maximum"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ValueError(f"{name} must be numeric")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        if self.allowed_values and (
            self.kind is not PropertyKind.STRING
            or any(not isinstance(v, str) or not v for v in self.allowed_values)
            or len(set(self.allowed_values)) != len(self.allowed_values)
        ):
            raise ValueError("allowed_values requires unique non-empty string values")

    def validate(self, value: Any) -> None:
        valid = {
            PropertyKind.STRING: lambda v: isinstance(v, str),
            PropertyKind.INTEGER: lambda v: isinstance(v, int) and not isinstance(v, bool),
            PropertyKind.NUMBER: lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            PropertyKind.BOOLEAN: lambda v: isinstance(v, bool),
            PropertyKind.STRING_LIST: lambda v: isinstance(v, (list, tuple))
            and all(isinstance(item, str) for item in v),
        }[self.kind](value)
        if not valid:
            raise ValueError(f"property {self.name!r} must be {self.kind.value}")
        if isinstance(value, str) and self.max_length is not None and len(value) > self.max_length:
            raise ValueError(f"property {self.name!r} exceeds max_length")
        if self.allowed_values and value not in self.allowed_values:
            raise ValueError(f"property {self.name!r} is not an allowed value")
        if self.kind in {PropertyKind.INTEGER, PropertyKind.NUMBER}:
            if self.minimum is not None and value < self.minimum:
                raise ValueError(f"property {self.name!r} is below minimum")
            if self.maximum is not None and value > self.maximum:
                raise ValueError(f"property {self.name!r} exceeds maximum")
        if self.kind is PropertyKind.STRING_LIST and self.max_length is not None:
            if any(len(item) > self.max_length for item in value):
                raise ValueError(f"property {self.name!r} contains an oversized item")


@dataclass(frozen=True)
class EntityTypeDefinition:
    name: str
    properties: tuple[PropertyDefinition, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "entity type")
        _validate_properties(self.properties)


@dataclass(frozen=True)
class RelationTypeDefinition:
    name: str
    source_types: tuple[str, ...]
    target_types: tuple[str, ...]
    properties: tuple[PropertyDefinition, ...] = ()
    directed: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        _validate_identifier(self.name, "relation type")
        if not self.source_types or not self.target_types:
            raise ValueError("relations must declare source_types and target_types")
        for value in (*self.source_types, *self.target_types):
            _validate_identifier(value, "endpoint entity type")
        if not isinstance(self.directed, bool):
            raise ValueError("directed must be a boolean")
        _validate_properties(self.properties)


def _validate_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a lowercase identifier (1-63 characters)")


def _validate_properties(properties: tuple[PropertyDefinition, ...]) -> None:
    if not isinstance(properties, tuple) or any(
        not isinstance(item, PropertyDefinition) for item in properties
    ):
        raise ValueError("properties must be a tuple of PropertyDefinition")
    names = [item.name for item in properties]
    if len(names) != len(set(names)):
        raise ValueError("property names must be unique")


@dataclass(frozen=True)
class SourceTypeMapping:
    """Maps a canonical source-domain record kind to its graph entity type."""

    domain: GraphSourceDomain
    source_type: str
    entity_type: str

    def __post_init__(self) -> None:
        if not isinstance(self.domain, GraphSourceDomain):
            raise ValueError("domain must be a GraphSourceDomain")
        _validate_identifier(self.source_type, "source type")
        _validate_identifier(self.entity_type, "mapped entity type")


@dataclass(frozen=True)
class GraphSchema:
    tenant_id: str
    entity_types: tuple[EntityTypeDefinition, ...]
    relation_types: tuple[RelationTypeDefinition, ...]
    source_mappings: tuple[SourceTypeMapping, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        if len({item.name for item in self.entity_types}) != len(self.entity_types):
            raise ValueError("entity type names must be unique")
        if len({item.name for item in self.relation_types}) != len(self.relation_types):
            raise ValueError("relation type names must be unique")
        entity_names = {item.name for item in self.entity_types}
        for relation in self.relation_types:
            if not set(relation.source_types + relation.target_types) <= entity_names:
                raise ValueError(f"relation {relation.name!r} references an unknown entity type")
        keys = [(item.domain, item.source_type) for item in self.source_mappings]
        if len(keys) != len(set(keys)):
            raise ValueError("source domain/type mappings must be unique")
        if any(item.entity_type not in entity_names for item in self.source_mappings):
            raise ValueError("source mapping references an unknown entity type")

    def entity_type(self, name: str) -> EntityTypeDefinition:
        for item in self.entity_types:
            if item.name == name:
                return item
        raise KeyError(f"unknown entity type: {name}")

    def relation_type(self, name: str) -> RelationTypeDefinition:
        for item in self.relation_types:
            if item.name == name:
                return item
        raise KeyError(f"unknown relation type: {name}")

    def validate_attributes(
        self, definitions: tuple[PropertyDefinition, ...], attributes: Mapping[str, Any]
    ) -> dict[str, Any]:
        if not isinstance(attributes, Mapping):
            raise ValueError("attributes must be a mapping")
        known = {item.name: item for item in definitions}
        unknown = set(attributes) - set(known)
        if unknown:
            raise ValueError(f"unknown properties: {', '.join(sorted(map(str, unknown)))}")
        result = dict(attributes)
        for name, definition in known.items():
            if name not in result:
                if definition.required:
                    raise ValueError(f"required property {name!r} is missing")
                continue
            definition.validate(result[name])
        return result


@dataclass(frozen=True)
class GraphEntity:
    tenant_id: str
    entity_id: str
    entity_type: str
    attributes: Mapping[str, Any]
    project_id: str | None = None

    def validate(self, schema: GraphSchema) -> None:
        _validate_same_tenant(self.tenant_id, schema.tenant_id)
        _validate_record_id(self.entity_id, "entity_id")
        schema.validate_attributes(schema.entity_type(self.entity_type).properties, self.attributes)
        if self.project_id is not None and (
            not isinstance(self.project_id, str) or not self.project_id.strip()
        ):
            raise ValueError("project_id must not be empty")


@dataclass(frozen=True)
class GraphRelation:
    tenant_id: str
    relation_id: str
    relation_type: str
    source_entity_id: str
    target_entity_id: str
    source_entity_type: str
    target_entity_type: str
    attributes: Mapping[str, Any] = field(default_factory=dict)
    project_id: str | None = None

    def validate(self, schema: GraphSchema) -> None:
        _validate_same_tenant(self.tenant_id, schema.tenant_id)
        for name in ("relation_id", "source_entity_id", "target_entity_id"):
            _validate_record_id(getattr(self, name), name)
        definition = schema.relation_type(self.relation_type)
        if self.source_entity_type not in definition.source_types:
            raise ValueError("source entity type is not allowed by relation schema")
        if self.target_entity_type not in definition.target_types:
            raise ValueError("target entity type is not allowed by relation schema")
        schema.validate_attributes(definition.properties, self.attributes)
        if self.project_id is not None and (
            not isinstance(self.project_id, str) or not self.project_id.strip()
        ):
            raise ValueError("project_id must not be empty")


def _validate_record_id(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 200:
        raise ValueError(f"{label} must contain 1-200 non-whitespace characters")


def _validate_same_tenant(actual: str, expected: str) -> None:
    if not isinstance(actual, str) or actual != expected:
        raise ValueError("graph record tenant does not match schema tenant")


def default_graph_schema(tenant_id: str) -> GraphSchema:
    """Return platform source mappings and minimal graph vocabulary for one tenant."""
    entities = (
        EntityTypeDefinition("document", (PropertyDefinition("title", PropertyKind.STRING),)),
        EntityTypeDefinition("memory", (PropertyDefinition("memory_scope", PropertyKind.STRING),)),
        EntityTypeDefinition("blackboard_entry", (PropertyDefinition("entry_kind", PropertyKind.STRING),)),
        EntityTypeDefinition("user"),
        EntityTypeDefinition("project"),
    )
    relations = (
        RelationTypeDefinition(
            "references", ("document", "memory", "blackboard_entry"),
            ("document", "memory", "blackboard_entry"),
        ),
        RelationTypeDefinition("member_of", ("user",), ("project",)),
    )
    mappings = (
        SourceTypeMapping(GraphSourceDomain.KNOWLEDGE, "document", "document"),
        SourceTypeMapping(GraphSourceDomain.MEMORY, "memory", "memory"),
        SourceTypeMapping(GraphSourceDomain.BLACKBOARD, "entry", "blackboard_entry"),
    )
    return GraphSchema(tenant_id, entities, relations, mappings)


class GraphSchemaRegistry:
    """In-process registry for tenant-owned runtime schema extensions.

    Persistence and distributed coordination are intentionally left to the
    graph storage task. A caller must load the tenant's schema before registering.
    """

    def __init__(self) -> None:
        self._schemas: dict[str, GraphSchema] = {}
        self._lock = RLock()

    def get(self, tenant_id: str) -> GraphSchema:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must not be empty")
        with self._lock:
            return self._schemas.get(tenant_id) or default_graph_schema(tenant_id)

    def register_entity_type(self, tenant_id: str, definition: EntityTypeDefinition) -> GraphSchema:
        if not isinstance(definition, EntityTypeDefinition):
            raise ValueError("definition must be an EntityTypeDefinition")
        with self._lock:
            current = self.get(tenant_id)
            if definition.name in _BUILTIN_TYPES:
                raise ValueError("built-in entity types cannot be overridden")
            if any(item.name == definition.name for item in current.entity_types):
                raise ValueError("entity type already exists")
            updated = GraphSchema(
                tenant_id, current.entity_types + (definition,),
                current.relation_types, current.source_mappings,
            )
            self._schemas[tenant_id] = updated
            return updated

    def register_relation_type(self, tenant_id: str, definition: RelationTypeDefinition) -> GraphSchema:
        if not isinstance(definition, RelationTypeDefinition):
            raise ValueError("definition must be a RelationTypeDefinition")
        with self._lock:
            current = self.get(tenant_id)
            if any(item.name == definition.name for item in current.relation_types):
                raise ValueError("relation type already exists")
            updated = GraphSchema(
                tenant_id, current.entity_types,
                current.relation_types + (definition,), current.source_mappings,
            )
            self._schemas[tenant_id] = updated
            return updated
