"""Typed, extensible schema contracts for the knowledge graph domain.

This module defines graph metadata only. It deliberately has no storage or
Blackboard side effects; adapters can consume these immutable contracts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping
from urllib.parse import urlsplit


_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")
_RECORD_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class GraphValueType(str, Enum):
    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATETIME = "datetime"
    STRING_LIST = "string_list"


class GraphSourceDomain(str, Enum):
    KNOWLEDGE = "knowledge"
    MEMORY = "memory"
    BLACKBOARD = "blackboard"
    FILE = "file"


@dataclass(frozen=True)
class GraphAttribute:
    """A single typed property constraint for an entity or relation."""

    name: str
    value_type: GraphValueType
    required: bool = False
    indexed: bool = False
    max_length: int | None = None
    allowed_values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _IDENTIFIER.fullmatch(self.name):
            raise ValueError("attribute name must be a canonical identifier")
        if not isinstance(self.value_type, GraphValueType):
            raise ValueError("attribute value_type must be a GraphValueType")
        if self.max_length is not None and (
            type(self.max_length) is not int or self.max_length < 1
        ):
            raise ValueError("max_length must be a positive integer")
        if self.allowed_values and self.value_type is not GraphValueType.STRING:
            raise ValueError("allowed_values are supported only for string attributes")
        if any(not isinstance(item, str) or not item for item in self.allowed_values):
            raise ValueError("allowed_values must contain non-empty strings")
        if len(set(self.allowed_values)) != len(self.allowed_values):
            raise ValueError("allowed_values must be unique")

    def validate(self, value: Any) -> None:
        """Raise ValueError when a runtime property violates this definition."""
        valid = {
            GraphValueType.STRING: lambda v: isinstance(v, str),
            GraphValueType.INTEGER: lambda v: type(v) is int,
            GraphValueType.NUMBER: lambda v: type(v) in (int, float),
            GraphValueType.BOOLEAN: lambda v: type(v) is bool,
            GraphValueType.DATETIME: lambda v: isinstance(v, datetime),
            GraphValueType.STRING_LIST: lambda v: (
                isinstance(v, (list, tuple)) and all(isinstance(x, str) for x in v)
            ),
        }[self.value_type](value)
        if not valid:
            raise ValueError(f"attribute {self.name} must be {self.value_type.value}")
        if self.max_length is not None:
            size = len(value) if isinstance(value, (str, list, tuple)) else None
            if size is not None and size > self.max_length:
                raise ValueError(f"attribute {self.name} exceeds max_length")
        if self.allowed_values and value not in self.allowed_values:
            raise ValueError(f"attribute {self.name} is not an allowed value")


@dataclass(frozen=True)
class GraphEntityType:
    """Schema for one entity kind and its permitted canonical source domains."""

    type_id: str
    description: str
    attributes: Mapping[str, GraphAttribute] = field(default_factory=dict)
    source_domains: tuple[GraphSourceDomain, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.type_id, str) or not _IDENTIFIER.fullmatch(self.type_id):
            raise ValueError("entity type_id must be a canonical identifier")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("entity description must not be empty")
        attrs = dict(self.attributes)
        if any(key != value.name for key, value in attrs.items()):
            raise ValueError("attribute mapping keys must match attribute names")
        if any(not isinstance(value, GraphAttribute) for value in attrs.values()):
            raise ValueError("entity attributes must be GraphAttribute values")
        if len(set(self.source_domains)) != len(self.source_domains):
            raise ValueError("source_domains must be unique")
        if any(not isinstance(item, GraphSourceDomain) for item in self.source_domains):
            raise ValueError("source_domains must contain GraphSourceDomain values")
        object.__setattr__(self, "attributes", MappingProxyType(attrs))

    def validate_attributes(self, values: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(values, Mapping):
            raise ValueError("entity attributes must be a mapping")
        unknown = set(values) - set(self.attributes)
        if unknown:
            raise ValueError(f"unknown attributes for {self.type_id}: {sorted(unknown)}")
        result = dict(values)
        for name, definition in self.attributes.items():
            if name not in result:
                if definition.required:
                    raise ValueError(f"missing required attribute: {name}")
                continue
            definition.validate(result[name])
        return result


@dataclass(frozen=True)
class GraphRelationType:
    """Schema for a directed relation and its allowed endpoint entity types."""

    type_id: str
    description: str
    source_types: tuple[str, ...]
    target_types: tuple[str, ...]
    attributes: Mapping[str, GraphAttribute] = field(default_factory=dict)
    directed: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.type_id, str) or not _IDENTIFIER.fullmatch(self.type_id):
            raise ValueError("relation type_id must be a canonical identifier")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("relation description must not be empty")
        if not self.source_types or not self.target_types:
            raise ValueError("relation endpoints must declare at least one entity type")
        if any(not _IDENTIFIER.fullmatch(item) for item in (*self.source_types, *self.target_types)):
            raise ValueError("relation endpoint types must be canonical identifiers")
        if len(set(self.source_types)) != len(self.source_types):
            raise ValueError("source_types must be unique")
        if len(set(self.target_types)) != len(self.target_types):
            raise ValueError("target_types must be unique")
        if type(self.directed) is not bool:
            raise ValueError("directed must be a boolean")
        attrs = dict(self.attributes)
        if any(key != value.name for key, value in attrs.items()):
            raise ValueError("attribute mapping keys must match attribute names")
        if any(not isinstance(value, GraphAttribute) for value in attrs.values()):
            raise ValueError("relation attributes must be GraphAttribute values")
        object.__setattr__(self, "attributes", MappingProxyType(attrs))

    def validate_endpoints(self, source_type: str, target_type: str) -> None:
        if source_type not in self.source_types or target_type not in self.target_types:
            raise ValueError(
                f"relation {self.type_id} does not allow {source_type} -> {target_type}"
            )


@dataclass(frozen=True)
class GraphSourceReference:
    """Tenant-bound, version-pinned pointer to a canonical source record."""

    domain: GraphSourceDomain
    tenant_id: str
    record_id: str
    version: int
    approved: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.domain, GraphSourceDomain):
            raise ValueError("source domain must be a GraphSourceDomain")
        for name in ("tenant_id", "record_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _RECORD_ID.fullmatch(value):
                raise ValueError(f"{name} must be a canonical identifier")
        if type(self.version) is not int or self.version < 1:
            raise ValueError("source version must be a positive integer")
        if type(self.approved) is not bool:
            raise ValueError("approved must be a boolean")
        if self.domain in (GraphSourceDomain.MEMORY, GraphSourceDomain.BLACKBOARD) and not self.approved:
            raise ValueError("only approved Memory items and Blackboard decisions may enter the graph")

    @property
    def uri(self) -> str:
        return f"{self.domain.value}://{self.tenant_id}/{self.record_id}@{self.version}"

    @classmethod
    def parse(cls, value: str, *, approved: bool = True) -> "GraphSourceReference":
        if not isinstance(value, str) or value != value.strip() or not value:
            raise ValueError("source reference must be a canonical non-empty URI")
        parsed = urlsplit(value)
        try:
            domain = GraphSourceDomain(parsed.scheme)
        except ValueError as exc:
            raise ValueError("unsupported graph source domain") from exc
        if (
            not parsed.netloc or parsed.username is not None or parsed.password is not None
            or parsed.port is not None or parsed.query or parsed.fragment
            or parsed.path.count("/") != 1 or not parsed.path.startswith("/")
        ):
            raise ValueError("source reference must contain tenant and record identity only")
        record_version = parsed.path[1:].rsplit("@", 1)
        if len(record_version) != 2:
            raise ValueError("source reference must pin an explicit version")
        record_id, raw_version = record_version
        if not raw_version.isdecimal() or str(int(raw_version)) != raw_version:
            raise ValueError("source version must be a canonical positive integer")
        reference = cls(domain, parsed.netloc, record_id, int(raw_version), approved)
        if reference.uri != value:
            raise ValueError("source reference is not canonical")
        return reference


@dataclass(frozen=True)
class GraphSchema:
    """Immutable registry; runtime extensions return a new validated schema."""

    entity_types: Mapping[str, GraphEntityType]
    relation_types: Mapping[str, GraphRelationType]

    def __post_init__(self) -> None:
        entities = dict(self.entity_types)
        relations = dict(self.relation_types)
        if any(key != value.type_id for key, value in entities.items()):
            raise ValueError("entity registry keys must match type_id")
        if any(key != value.type_id for key, value in relations.items()):
            raise ValueError("relation registry keys must match type_id")
        if set(entities) & set(relations):
            raise ValueError("entity and relation type identifiers must be distinct")
        for relation in relations.values():
            missing = (set(relation.source_types) | set(relation.target_types)) - set(entities)
            if missing:
                raise ValueError(f"relation {relation.type_id} references unknown types: {sorted(missing)}")
        object.__setattr__(self, "entity_types", MappingProxyType(entities))
        object.__setattr__(self, "relation_types", MappingProxyType(relations))

    def register_entity_type(self, definition: GraphEntityType) -> "GraphSchema":
        if not isinstance(definition, GraphEntityType):
            raise TypeError("definition must be a GraphEntityType")
        if definition.type_id in self.entity_types or definition.type_id in self.relation_types:
            raise ValueError(f"graph type already registered: {definition.type_id}")
        return GraphSchema(
            {**self.entity_types, definition.type_id: definition},
            self.relation_types,
        )

    def register_relation_type(self, definition: GraphRelationType) -> "GraphSchema":
        if not isinstance(definition, GraphRelationType):
            raise TypeError("definition must be a GraphRelationType")
        if definition.type_id in self.entity_types or definition.type_id in self.relation_types:
            raise ValueError(f"graph type already registered: {definition.type_id}")
        return GraphSchema(
            self.entity_types,
            {**self.relation_types, definition.type_id: definition},
        )

    def validate_entity(
        self, *, type_id: str, tenant_id: str, attributes: Mapping[str, Any],
        source: GraphSourceReference,
    ) -> dict[str, Any]:
        definition = self.entity_types.get(type_id)
        if definition is None:
            raise ValueError(f"unknown entity type: {type_id}")
        if not isinstance(tenant_id, str) or not _RECORD_ID.fullmatch(tenant_id):
            raise ValueError("tenant_id must be a canonical identifier")
        if not isinstance(source, GraphSourceReference):
            raise TypeError("source must be a GraphSourceReference")
        if source.tenant_id != tenant_id:
            raise ValueError("source tenant must match graph entity tenant")
        if source.domain not in definition.source_domains:
            raise ValueError(f"source domain is not allowed for {type_id}")
        return definition.validate_attributes(attributes)

    def validate_relation(
        self, *, type_id: str, source_type: str, target_type: str,
        attributes: Mapping[str, Any],
    ) -> dict[str, Any]:
        definition = self.relation_types.get(type_id)
        if definition is None:
            raise ValueError(f"unknown relation type: {type_id}")
        definition.validate_endpoints(source_type, target_type)
        return _validate_attributes(definition.attributes, attributes, type_id)


def _validate_attributes(
    definitions: Mapping[str, GraphAttribute], values: Mapping[str, Any], owner: str,
) -> dict[str, Any]:
    if not isinstance(values, Mapping):
        raise ValueError(f"{owner} attributes must be a mapping")
    unknown = set(values) - set(definitions)
    if unknown:
        raise ValueError(f"unknown attributes for {owner}: {sorted(unknown)}")
    result = dict(values)
    for name, definition in definitions.items():
        if name not in result:
            if definition.required:
                raise ValueError(f"missing required attribute: {name}")
            continue
        definition.validate(result[name])
    return result


def default_graph_schema() -> GraphSchema:
    """Return the built-in cross-domain schema; callers may extend it immutably."""
    string_id = GraphAttribute("canonical_id", GraphValueType.STRING, required=True, indexed=True, max_length=128)
    title = GraphAttribute("title", GraphValueType.STRING, required=True, max_length=512)
    confidence = GraphAttribute("confidence", GraphValueType.NUMBER)
    status = GraphAttribute(
        "status", GraphValueType.STRING, allowed_values=("approved", "active", "archived")
    )
    entities = {
        "KnowledgeDocument": GraphEntityType(
            "KnowledgeDocument", "Versioned canonical knowledge document.",
            {"canonical_id": string_id, "title": title, "status": status},
            (GraphSourceDomain.KNOWLEDGE,),
        ),
        "KnowledgeChunk": GraphEntityType(
            "KnowledgeChunk", "A source-located chunk belonging to a knowledge document.",
            {
                "canonical_id": string_id,
                "text": GraphAttribute("text", GraphValueType.STRING, required=True, max_length=20000),
                "ordinal": GraphAttribute("ordinal", GraphValueType.INTEGER, required=True),
            },
            (GraphSourceDomain.KNOWLEDGE,),
        ),
        "MemoryItem": GraphEntityType(
            "MemoryItem", "An approved durable memory record; never a transient candidate.",
            {
                "canonical_id": string_id, "title": title,
                "scope": GraphAttribute(
                    "scope", GraphValueType.STRING, required=True,
                    allowed_values=("user", "agent", "project", "tenant"),
                ),
                "confidence": confidence,
            },
            (GraphSourceDomain.MEMORY,),
        ),
        "BlackboardDecision": GraphEntityType(
            "BlackboardDecision", "An approved, versioned Blackboard decision.",
            {"canonical_id": string_id, "title": title, "status": status},
            (GraphSourceDomain.BLACKBOARD,),
        ),
        "FileAsset": GraphEntityType(
            "FileAsset", "A versioned file asset; graph stores metadata and source reference only.",
            {
                "canonical_id": string_id, "title": title,
                "media_type": GraphAttribute("media_type", GraphValueType.STRING, max_length=255),
            },
            (GraphSourceDomain.FILE,),
        ),
    }
    relations = {
        "CONTAINS": GraphRelationType(
            "CONTAINS", "Document contains a source-located chunk.",
            ("KnowledgeDocument",), ("KnowledgeChunk",),
        ),
        "DERIVED_FROM": GraphRelationType(
            "DERIVED_FROM", "A graph fact is derived from a canonical source.",
            ("MemoryItem", "BlackboardDecision", "KnowledgeDocument", "KnowledgeChunk"),
            ("KnowledgeDocument", "KnowledgeChunk", "FileAsset"),
            {"confidence": confidence},
        ),
        "REFERENCES": GraphRelationType(
            "REFERENCES", "An approved Blackboard decision references a domain record.",
            ("BlackboardDecision",),
            ("KnowledgeDocument", "KnowledgeChunk", "MemoryItem", "FileAsset"),
        ),
    }
    return GraphSchema(entities, relations)
