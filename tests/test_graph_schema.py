from __future__ import annotations

import pytest

from agent_platform.graph.schema import (
    GraphAttribute,
    GraphEntityType,
    GraphRelationType,
    GraphSourceDomain,
    GraphSourceReference,
    GraphValueType,
    default_graph_schema,
)


def test_default_schema_maps_all_canonical_domains() -> None:
    schema = default_graph_schema()
    assert schema.entity_types["KnowledgeDocument"].source_domains == (
        GraphSourceDomain.KNOWLEDGE,
    )
    assert schema.entity_types["KnowledgeChunk"].source_domains == (
        GraphSourceDomain.KNOWLEDGE,
    )
    assert schema.entity_types["MemoryItem"].source_domains == (
        GraphSourceDomain.MEMORY,
    )
    assert schema.entity_types["BlackboardDecision"].source_domains == (
        GraphSourceDomain.BLACKBOARD,
    )
    assert schema.entity_types["FileAsset"].source_domains == (
        GraphSourceDomain.FILE,
    )
    assert {"CONTAINS", "DERIVED_FROM", "REFERENCES"} <= set(schema.relation_types)


def test_source_reference_round_trip_and_tenant_binding() -> None:
    source = GraphSourceReference.parse("knowledge://tenant-a/doc-1@2")
    assert source.uri == "knowledge://tenant-a/doc-1@2"
    attrs = default_graph_schema().validate_entity(
        type_id="KnowledgeDocument",
        tenant_id="tenant-a",
        attributes={"canonical_id": "doc-1", "title": "Runbook"},
        source=source,
    )
    assert attrs["title"] == "Runbook"


@pytest.mark.parametrize(
    "uri",
    [
        "",
        "knowledge://tenant-a/doc-1",
        "knowledge://tenant-a/doc-1@0",
        "knowledge://tenant-a/doc-1@01",
        "knowledge://user@tenant-a/doc-1@1",
        "knowledge://tenant-a:443/doc-1@1",
        "knowledge://tenant-a/../doc-1@1",
        "knowledge://tenant-a/doc-1@1?payload=1",
        "unknown://tenant-a/doc-1@1",
    ],
)
def test_source_reference_rejects_noncanonical_uri(uri: str) -> None:
    with pytest.raises(ValueError):
        GraphSourceReference.parse(uri)


def test_blackboard_sources_must_be_approved() -> None:
    with pytest.raises(ValueError, match="approved Blackboard decisions"):
        GraphSourceReference(
            GraphSourceDomain.BLACKBOARD, "tenant-a", "decision-1", 1, False
        )


def test_entity_validation_rejects_cross_tenant_and_wrong_domain() -> None:
    schema = default_graph_schema()
    with pytest.raises(ValueError, match="tenant must match"):
        schema.validate_entity(
            type_id="KnowledgeDocument",
            tenant_id="tenant-a",
            attributes={"canonical_id": "doc-1", "title": "Doc"},
            source=GraphSourceReference(
                GraphSourceDomain.KNOWLEDGE, "tenant-b", "doc-1", 1
            ),
        )
    with pytest.raises(ValueError, match="source domain is not allowed"):
        schema.validate_entity(
            type_id="KnowledgeDocument",
            tenant_id="tenant-a",
            attributes={"canonical_id": "doc-1", "title": "Doc"},
            source=GraphSourceReference(
                GraphSourceDomain.MEMORY, "tenant-a", "memory-1", 1
            ),
        )


def test_entity_attribute_required_type_enum_and_length_constraints() -> None:
    schema = default_graph_schema()
    source = GraphSourceReference(
        GraphSourceDomain.KNOWLEDGE, "tenant-a", "doc-1", 1
    )
    with pytest.raises(ValueError, match="missing required"):
        schema.validate_entity(
            type_id="KnowledgeChunk", tenant_id="tenant-a", attributes={},
            source=source,
        )
    with pytest.raises(ValueError, match="must be integer"):
        schema.validate_entity(
            type_id="KnowledgeChunk", tenant_id="tenant-a",
            attributes={"canonical_id": "chunk-1", "text": "x", "ordinal": True},
            source=GraphSourceReference(
                GraphSourceDomain.KNOWLEDGE, "tenant-a", "chunk-1", 1
            ),
        )
    with pytest.raises(ValueError, match="exceeds max_length"):
        schema.validate_entity(
            type_id="KnowledgeChunk", tenant_id="tenant-a",
            attributes={
                "canonical_id": "chunk-1", "text": "x" * 20001, "ordinal": 0,
            },
            source=GraphSourceReference(
                GraphSourceDomain.KNOWLEDGE, "tenant-a", "chunk-1", 1
            ),
        )
    with pytest.raises(ValueError, match="not an allowed value"):
        schema.validate_entity(
            type_id="MemoryItem", tenant_id="tenant-a",
            attributes={
                "canonical_id": "memory-1", "title": "Fact", "scope": "global",
            },
            source=GraphSourceReference(
                GraphSourceDomain.MEMORY, "tenant-a", "memory-1", 1
            ),
        )


def test_runtime_entity_and_relation_extensions_are_immutable() -> None:
    original = default_graph_schema()
    entity = GraphEntityType(
        "Product", "A product entity.",
        {"name": GraphAttribute("name", GraphValueType.STRING, required=True)},
        (GraphSourceDomain.KNOWLEDGE,),
    )
    extended = original.register_entity_type(entity)
    assert "Product" not in original.entity_types
    assert "Product" in extended.entity_types
    relation = GraphRelationType(
        "USES_PRODUCT", "An agent uses a product.",
        ("MemoryItem",), ("Product",),
    )
    final = extended.register_relation_type(relation)
    assert "USES_PRODUCT" not in extended.relation_types
    assert "USES_PRODUCT" in final.relation_types
    assert final.validate_relation(
        type_id="USES_PRODUCT", source_type="MemoryItem", target_type="Product",
        attributes={},
    ) == {}


def test_runtime_extensions_reject_duplicate_and_unknown_endpoint_types() -> None:
    schema = default_graph_schema()
    with pytest.raises(ValueError, match="already registered"):
        schema.register_entity_type(schema.entity_types["MemoryItem"])
    with pytest.raises(ValueError, match="unknown types"):
        schema.register_relation_type(
            GraphRelationType(
                "UNKNOWN_EDGE", "Invalid edge.", ("Missing",), ("MemoryItem",)
            )
        )


def test_relation_endpoint_and_attribute_validation() -> None:
    schema = default_graph_schema()
    with pytest.raises(ValueError, match="does not allow"):
        schema.validate_relation(
            type_id="CONTAINS", source_type="MemoryItem",
            target_type="KnowledgeChunk", attributes={},
        )
    with pytest.raises(ValueError, match="must be number"):
        schema.validate_relation(
            type_id="DERIVED_FROM", source_type="MemoryItem",
            target_type="KnowledgeChunk", attributes={"confidence": "high"},
        )
    with pytest.raises(ValueError, match="unknown attributes"):
        schema.validate_relation(
            type_id="CONTAINS", source_type="KnowledgeDocument",
            target_type="KnowledgeChunk", attributes={"secret": "x"},
        )


def test_schema_mappings_are_read_only() -> None:
    schema = default_graph_schema()
    with pytest.raises(TypeError):
        schema.entity_types["Injected"] = schema.entity_types["MemoryItem"]
    with pytest.raises(TypeError):
        schema.entity_types["MemoryItem"].attributes["injected"] = GraphAttribute(
            "injected", GraphValueType.STRING
        )
