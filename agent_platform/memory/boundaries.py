"""Explicit source boundaries between durable memory and canonical platform domains.

Memory stores durable, approved distilled facts. Knowledge, Blackboard, and session
history remain the systems of record for their own content. Memory keeps typed
references to those records rather than copying their source payloads.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit


class MemorySourceDomain(str, Enum):
    KNOWLEDGE = "knowledge"
    BLACKBOARD = "blackboard"
    SESSION = "session"


_REFERENCE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class MemorySourceReference:
    """Stable pointer to a canonical record owned by another domain."""

    domain: MemorySourceDomain
    tenant_id: str
    record_id: str
    version: int
    approved: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.domain, MemorySourceDomain):
            raise ValueError("domain must be a supported memory source domain")
        for name in ("tenant_id", "record_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _REFERENCE_ID.fullmatch(value):
                raise ValueError(
                    f"{name} must be a canonical identifier containing only "
                    "letters, digits, dots, underscores, or hyphens"
                )
        if (
            isinstance(self.version, bool)
            or not isinstance(self.version, int)
            or self.version < 1
        ):
            raise ValueError("source version must be a positive integer")
        if not isinstance(self.approved, bool):
            raise ValueError("approved must be a boolean")
        if self.domain is MemorySourceDomain.BLACKBOARD and not self.approved:
            raise ValueError("memory may only cite approved Blackboard decisions")

    @property
    def uri(self) -> str:
        return (
            f"{self.domain.value}://{self.tenant_id}/"
            f"{self.record_id}@{self.version}"
        )

    @classmethod
    def parse(
        cls, value: str, *, approved: bool = True
    ) -> "MemorySourceReference":
        if not isinstance(value, str) or not value:
            raise ValueError("source reference must be a non-empty string")
        if value != value.strip():
            raise ValueError("source reference must not contain surrounding whitespace")

        parsed = urlsplit(value)
        try:
            domain = MemorySourceDomain(parsed.scheme)
        except ValueError as exc:
            raise ValueError(
                "source reference must target knowledge, blackboard, or session"
            ) from exc

        # Reject user-info, ports, encoded authority tricks, and any non-canonical
        # path shape instead of interpreting them as tenant/record identifiers.
        if (
            parsed.query
            or parsed.fragment
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.path.count("/") != 1
            or not parsed.path.startswith("/")
        ):
            raise ValueError(
                "source reference must contain tenant and record identity only"
            )

        record_version = parsed.path[1:].rsplit("@", 1)
        if len(record_version) != 2:
            raise ValueError("source reference must pin an explicit version")
        record_id, raw_version = record_version
        if not raw_version.isdecimal() or str(int(raw_version)) != raw_version:
            raise ValueError("source version must be a canonical positive integer")

        reference = cls(
            domain=domain,
            tenant_id=parsed.netloc,
            record_id=record_id,
            version=int(raw_version),
            approved=approved,
        )
        if reference.uri != value:
            raise ValueError("source reference is not in canonical form")
        return reference


class MemoryBoundaryPolicy:
    """Enforce one-way references and prevent domain responsibility overlap."""

    @staticmethod
    def validate_reference(
        value: str, *, approved: bool = True
    ) -> MemorySourceReference:
        return MemorySourceReference.parse(value, approved=approved)

    @staticmethod
    def responsibility(domain: MemorySourceDomain) -> str:
        return {
            MemorySourceDomain.KNOWLEDGE: (
                "Canonical documents, chunks, source versions, ACLs, and retrieval citations."
            ),
            MemorySourceDomain.BLACKBOARD: (
                "Transient expert collaboration, proposals, shared working state, and decisions."
            ),
            MemorySourceDomain.SESSION: (
                "Append-only conversation messages, turns, and session chronology."
            ),
        }[domain]

    @staticmethod
    def memory_may_retain(domain: MemorySourceDomain) -> str:
        return {
            MemorySourceDomain.KNOWLEDGE: (
                "A distilled durable fact plus a pinned document/chunk reference; "
                "never a copied document."
            ),
            MemorySourceDomain.BLACKBOARD: (
                "A durable outcome from an approved decision plus its versioned "
                "decision reference; never drafts."
            ),
            MemorySourceDomain.SESSION: (
                "A durable fact extracted from a turn plus a pinned message/turn "
                "reference; never a transcript."
            ),
        }[domain]


class MemoryLayer(str, Enum):
    """Logical memory layers; not every layer is durably persisted."""

    USER = "user"
    AGENT = "agent"
    PROJECT = "project"
    ORGANIZATION = "organization"
    RUN = "run"
    SESSION = "session"


@dataclass(frozen=True)
class MemoryLayerBoundary:
    """Ownership and persistence contract for one logical memory layer."""

    layer: MemoryLayer
    owner: str
    persistence: str
    exclusions: str


MEMORY_BOUNDARIES = (
    MemoryLayerBoundary(
        MemoryLayer.USER,
        "The individual user within one tenant.",
        "MemoryScope.USER: durable user-approved preferences and facts.",
        "No credentials, secrets, or facts belonging to another user.",
    ),
    MemoryLayerBoundary(
        MemoryLayer.AGENT,
        "A named agent within one tenant.",
        "MemoryScope.AGENT: durable agent operating knowledge and approved lessons.",
        "No user-private facts or cross-tenant sharing.",
    ),
    MemoryLayerBoundary(
        MemoryLayer.PROJECT,
        "A project and its authorized members within one tenant.",
        "MemoryScope.PROJECT: durable project facts and decisions.",
        "No unrelated project data or cross-tenant sharing.",
    ),
    MemoryLayerBoundary(
        MemoryLayer.ORGANIZATION,
        "The organization boundary represented by one tenant.",
        "MemoryScope.TENANT: durable organization-wide facts for one tenant.",
        "No cross-tenant sharing or implicit access to another tenant.",
    ),
    MemoryLayerBoundary(
        MemoryLayer.RUN,
        "A single execution run and its runtime context.",
        "Ephemeral run context; not persisted to the durable memory store.",
        "No promotion to durable memory without extraction, review, and approval.",
    ),
    MemoryLayerBoundary(
        MemoryLayer.SESSION,
        "A conversation session and its message history.",
        "Ephemeral session context; canonical history remains in the session store, not the memory store.",
        "No transcript duplication or promotion without extraction and approval.",
    ),
)


def memory_boundary(layer: MemoryLayer) -> MemoryLayerBoundary:
    """Return the canonical contract for a typed memory layer."""
    if not isinstance(layer, MemoryLayer):
        raise TypeError("layer must be a MemoryLayer")
    return next(item for item in MEMORY_BOUNDARIES if item.layer is layer)


# String values intentionally mirror MemoryScope without importing service.py:
# service.py imports this module to validate source references, so importing its
# enum here would create a circular dependency.
MEMORY_SCOPE_BY_LAYER: dict[MemoryLayer, str] = {
    MemoryLayer.USER: "user",
    MemoryLayer.AGENT: "agent",
    MemoryLayer.PROJECT: "project",
    MemoryLayer.ORGANIZATION: "tenant",
}


def durable_scope_value(layer: MemoryLayer) -> str:
    """Return the MemoryScope value for a durable layer.

    RUN and SESSION are deliberately ephemeral and cannot be persisted as
    cross-session MemoryScope records.
    """
    if not isinstance(layer, MemoryLayer):
        raise TypeError("layer must be a MemoryLayer")
    try:
        return MEMORY_SCOPE_BY_LAYER[layer]
    except KeyError as exc:
        raise ValueError(
            f"{layer.value} context is ephemeral and has no durable MemoryScope"
        ) from exc


def is_durable_memory_layer(layer: MemoryLayer) -> bool:
    """Whether a logical layer is eligible for durable MemoryService storage."""
    if not isinstance(layer, MemoryLayer):
        raise TypeError("layer must be a MemoryLayer")
    return layer in MEMORY_SCOPE_BY_LAYER
