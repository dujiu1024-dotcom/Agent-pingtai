"""Canonical ownership and lifecycle boundaries for platform memory layers.

Only durable layers map to MemoryScope in the governed MemoryService. Run and
session working memory remain execution/session state and must not be persisted
as long-term MemoryCandidate records by implicit promotion.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class MemoryLayer(str, Enum):
    RUN = "run"
    SESSION = "session"
    USER = "user"
    AGENT = "agent"
    PROJECT = "project"
    ORGANIZATION = "organization"


@dataclass(frozen=True)
class MemoryBoundary:
    layer: MemoryLayer
    owner: str
    visibility: str
    lifetime: str
    persistence: str
    promotion_rule: str
    exclusions: tuple[str, ...]


MEMORY_BOUNDARIES: tuple[MemoryBoundary, ...] = (
    MemoryBoundary(
        layer=MemoryLayer.RUN,
        owner="one Run execution and its authenticated tenant/principal",
        visibility="only the active Run and its authorized child steps",
        lifetime="Run lifetime; discarded or checkpointed under Run recovery policy",
        persistence="ephemeral execution state/checkpoint, never durable user memory",
        promotion_rule="a separate, explicit candidate may be proposed from an eligible turn",
        exclusions=("cross-Run recall", "cross-session profile facts", "organization policy"),
    ),
    MemoryBoundary(
        layer=MemoryLayer.SESSION,
        owner="one conversation/session within one tenant",
        visibility="participants authorized for that session",
        lifetime="session lifetime plus the session-history retention policy",
        persistence="session/message history; not the long-term MemoryService",
        promotion_rule="eligible facts may be proposed as candidates with source references",
        exclusions=("automatic user-profile updates", "Agent-wide instructions", "project decisions"),
    ),
    MemoryBoundary(
        layer=MemoryLayer.USER,
        owner="one user within one tenant",
        visibility="that user; explicitly authorized operators for governed management",
        lifetime="until corrected, forgotten, expired, or retained under applicable policy",
        persistence="durable governed memory; maps to MemoryScope.USER",
        promotion_rule="candidate confirmation is required before retrieval",
        exclusions=("other users' private facts", "project/Agent/organization defaults"),
    ),
    MemoryBoundary(
        layer=MemoryLayer.AGENT,
        owner="one Agent identity within one tenant",
        visibility="authorized Runs using that Agent; management requires Agent-scoped grant",
        lifetime="Agent lifecycle, subject to versioning/archive policy",
        persistence="durable governed memory; maps to MemoryScope.AGENT",
        promotion_rule="explicit Agent-scope authorization and candidate confirmation",
        exclusions=("personal user preferences", "project decisions", "tenant-wide policy"),
    ),
    MemoryBoundary(
        layer=MemoryLayer.PROJECT,
        owner="one project within one tenant",
        visibility="project members with access to the project and its memory",
        lifetime="project lifecycle, subject to archive/retention policy",
        persistence="durable governed memory; maps to MemoryScope.PROJECT",
        promotion_rule="project-scoped management grant and candidate confirmation",
        exclusions=("facts private to a user", "Agent-only operating state", "cross-project sharing"),
    ),
    MemoryBoundary(
        layer=MemoryLayer.ORGANIZATION,
        owner="one organization security boundary represented by a tenant",
        visibility="principals authorized for organization-shared memory within that tenant",
        lifetime="organization policy lifecycle, subject to governance and retention",
        persistence="durable governed memory; maps to MemoryScope.TENANT",
        promotion_rule="explicit memory.manage authorization and candidate confirmation",
        exclusions=("cross-tenant sharing", "implicit access by every platform tenant", "personal facts"),
    ),
)

_BOUNDARY_BY_LAYER = {boundary.layer: boundary for boundary in MEMORY_BOUNDARIES}


def memory_boundary(layer: MemoryLayer) -> MemoryBoundary:
    """Return the canonical boundary for a memory layer."""
    if not isinstance(layer, MemoryLayer):
        raise TypeError("layer must be a MemoryLayer")
    return _BOUNDARY_BY_LAYER[layer]


__all__ = ["MEMORY_BOUNDARIES", "MemoryBoundary", "MemoryLayer", "memory_boundary"]
