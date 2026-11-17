from __future__ import annotations

import pytest

from agent_platform.memory.boundaries import (
    MemoryBoundaryPolicy,
    MemorySourceDomain,
    MemorySourceReference,
    MEMORY_BOUNDARIES,
    MemoryLayer,
    memory_boundary,
    durable_scope_value,
    is_durable_memory_layer,
)
from agent_platform.memory.service import MemoryCandidate, MemoryScope


@pytest.mark.parametrize(
    ("uri", "domain"),
    [
        ("knowledge://tenant-a/doc-17@3", MemorySourceDomain.KNOWLEDGE),
        ("blackboard://tenant-a/decision-8@2", MemorySourceDomain.BLACKBOARD),
        ("session://tenant-a/message-91@1", MemorySourceDomain.SESSION),
    ],
)
def test_source_reference_round_trip(uri: str, domain: MemorySourceDomain) -> None:
    reference = MemoryBoundaryPolicy.validate_reference(uri)
    assert reference.domain is domain
    assert reference.uri == uri


def test_blackboard_reference_requires_approved_decision() -> None:
    with pytest.raises(ValueError, match="approved Blackboard decisions"):
        MemorySourceReference(
            MemorySourceDomain.BLACKBOARD, "tenant-a", "proposal-2", 1, False
        )


@pytest.mark.parametrize(
    "uri",
    [
        "memory://tenant-a/memory-1@1",
        "knowledge://tenant-a/doc-1",
        "knowledge://tenant-a/doc-1@0",
        "knowledge://tenant-a/doc-1@01",
        "knowledge://tenant-a/doc-1@1?copy=payload",
        "knowledge://tenant-a/doc-1@1#fragment",
        "knowledge://user@tenant-a/doc-1@1",
        "knowledge://tenant-a:443/doc-1@1",
        "knowledge://tenant-a//doc-1@1",
        "knowledge://tenant-a/doc%2F1@1",
        "knowledge://tenant-a/../doc-1@1",
        "knowledge://tenant-a/@1",
        "session:///message-1@1",
        " session://tenant-a/message-1@1",
        "",
    ],
)
def test_rejects_unpinned_or_noncanonical_source_references(uri: str) -> None:
    with pytest.raises(ValueError):
        MemoryBoundaryPolicy.validate_reference(uri)


@pytest.mark.parametrize("identifier", ["", " tenant-a", "tenant/a", "tenant@a", "tenant:a"])
def test_source_reference_rejects_ambiguous_identifiers(identifier: str) -> None:
    with pytest.raises(ValueError):
        MemorySourceReference(
            MemorySourceDomain.KNOWLEDGE, identifier, "doc-1", 1
        )


def test_boundary_contract_keeps_domain_responsibilities_distinct() -> None:
    assert "documents, chunks" in MemoryBoundaryPolicy.responsibility(
        MemorySourceDomain.KNOWLEDGE
    )
    assert "proposals" in MemoryBoundaryPolicy.responsibility(
        MemorySourceDomain.BLACKBOARD
    )
    assert "messages, turns" in MemoryBoundaryPolicy.responsibility(
        MemorySourceDomain.SESSION
    )
    assert "never a transcript" in MemoryBoundaryPolicy.memory_may_retain(
        MemorySourceDomain.SESSION
    )


def test_memory_candidate_validates_canonical_reference_and_tenant() -> None:
    candidate = MemoryCandidate(
        tenant_id="tenant-a",
        scope=MemoryScope.USER,
        scope_id="user-1",
        content="prefers concise summaries",
        source_ref="session://tenant-a/message-91@1",
        confidence=0.9,
        created_by="user-1",
    )
    assert candidate.source_ref == "session://tenant-a/message-91@1"
    with pytest.raises(ValueError, match="tenant must match"):
        MemoryCandidate(
            tenant_id="tenant-a",
            scope=MemoryScope.USER,
            scope_id="user-1",
            content="fact",
            source_ref="knowledge://tenant-b/doc-2@1",
            confidence=0.8,
            created_by="user-1",
        )


def test_memory_candidate_rejects_unapproved_blackboard_reference() -> None:
    with pytest.raises(ValueError, match="approved Blackboard decisions"):
        MemoryCandidate(
            tenant_id="tenant-a",
            scope=MemoryScope.PROJECT,
            scope_id="project-1",
            content="decision outcome",
            source_ref="blackboard://tenant-a/decision-2@1",
            confidence=0.9,
            created_by="agent-1",
            source_approved=False,
        )


def test_six_memory_layers_have_canonical_ownership_boundaries() -> None:
    assert {item.layer for item in MEMORY_BOUNDARIES} == set(MemoryLayer)
    assert len(MEMORY_BOUNDARIES) == len(MemoryLayer)


def test_run_and_session_are_not_durable_memory_scopes() -> None:
    for layer in (MemoryLayer.RUN, MemoryLayer.SESSION):
        assert "MemoryScope." not in memory_boundary(layer).persistence


def test_durable_memory_layers_map_to_existing_scopes() -> None:
    expected = {
        MemoryLayer.USER: "MemoryScope.USER",
        MemoryLayer.AGENT: "MemoryScope.AGENT",
        MemoryLayer.PROJECT: "MemoryScope.PROJECT",
        MemoryLayer.ORGANIZATION: "MemoryScope.TENANT",
    }
    for layer, scope in expected.items():
        assert scope in memory_boundary(layer).persistence


def test_organization_memory_is_tenant_bounded() -> None:
    boundary = memory_boundary(MemoryLayer.ORGANIZATION)
    assert "one tenant" in boundary.owner
    assert "cross-tenant sharing" in boundary.exclusions


def test_memory_boundary_rejects_untyped_layer() -> None:
    with pytest.raises(TypeError):
        memory_boundary("user")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("layer", "scope"),
    [
        (MemoryLayer.USER, MemoryScope.USER),
        (MemoryLayer.AGENT, MemoryScope.AGENT),
        (MemoryLayer.PROJECT, MemoryScope.PROJECT),
        (MemoryLayer.ORGANIZATION, MemoryScope.TENANT),
    ],
)
def test_durable_layer_mapping_is_executable(
    layer: MemoryLayer, scope: MemoryScope,
) -> None:
    assert is_durable_memory_layer(layer)
    assert durable_scope_value(layer) == scope.value


@pytest.mark.parametrize("layer", [MemoryLayer.RUN, MemoryLayer.SESSION])
def test_ephemeral_layers_cannot_be_mapped_to_durable_memory_scope(
    layer: MemoryLayer,
) -> None:
    assert not is_durable_memory_layer(layer)
    with pytest.raises(ValueError, match="ephemeral"):
        durable_scope_value(layer)


@pytest.mark.parametrize("layer", ["user", None, 1])
def test_layer_mapping_rejects_untyped_values(layer: object) -> None:
    with pytest.raises(TypeError):
        is_durable_memory_layer(layer)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        durable_scope_value(layer)  # type: ignore[arg-type]
