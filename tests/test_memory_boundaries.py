from __future__ import annotations

import pytest

from agent_platform.memory.boundaries import MemoryBoundaryPolicy, MemorySourceDomain, MemorySourceReference
from agent_platform.memory.service import MemoryCandidate, MemoryScope


@pytest.mark.parametrize(("uri", "domain"), [
    ("knowledge://tenant-a/doc-17@3", MemorySourceDomain.KNOWLEDGE),
    ("blackboard://tenant-a/decision-8@2", MemorySourceDomain.BLACKBOARD),
    ("session://tenant-a/message-91@1", MemorySourceDomain.SESSION),
])
def test_source_reference_round_trip(uri: str, domain: MemorySourceDomain) -> None:
    reference = MemoryBoundaryPolicy.validate_reference(uri)
    assert reference.domain is domain
    assert reference.uri == uri


def test_blackboard_reference_requires_approved_decision() -> None:
    with pytest.raises(ValueError, match="approved Blackboard decisions"):
        MemorySourceReference(MemorySourceDomain.BLACKBOARD, "tenant-a", "proposal-2", 1, False)


@pytest.mark.parametrize("uri", [
    "memory://tenant-a/memory-1@1",
    "knowledge://tenant-a/doc-1",
    "knowledge://tenant-a/doc-1@0",
    "knowledge://tenant-a/doc-1@1?copy=payload",
    "session:///message-1@1",
    "",
])
def test_rejects_unpinned_or_noncanonical_source_references(uri: str) -> None:
    with pytest.raises(ValueError):
        MemoryBoundaryPolicy.validate_reference(uri)


def test_boundary_contract_keeps_domain_responsibilities_distinct() -> None:
    assert "documents, chunks" in MemoryBoundaryPolicy.responsibility(MemorySourceDomain.KNOWLEDGE)
    assert "proposals" in MemoryBoundaryPolicy.responsibility(MemorySourceDomain.BLACKBOARD)
    assert "messages, turns" in MemoryBoundaryPolicy.responsibility(MemorySourceDomain.SESSION)
    assert "never a transcript" in MemoryBoundaryPolicy.memory_may_retain(MemorySourceDomain.SESSION)

def test_memory_candidate_validates_canonical_reference_and_tenant() -> None:
    candidate = MemoryCandidate(
        tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-1",
        content="prefers concise summaries",
        source_ref="session://tenant-a/message-91@1", confidence=0.9, created_by="user-1",
    )
    assert candidate.source_ref == "session://tenant-a/message-91@1"
    with pytest.raises(ValueError, match="tenant must match"):
        MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.USER, scope_id="user-1",
            content="fact", source_ref="knowledge://tenant-b/doc-2@1",
            confidence=0.8, created_by="user-1",
        )

def test_memory_candidate_rejects_unapproved_blackboard_reference() -> None:
    with pytest.raises(ValueError, match="approved Blackboard decisions"):
        MemoryCandidate(
            tenant_id="tenant-a", scope=MemoryScope.PROJECT, scope_id="project-1",
            content="decision outcome", source_ref="blackboard://tenant-a/decision-2@1",
            confidence=0.9, created_by="agent-1", source_approved=False,
        )
