"""Explicit source boundaries between durable memory and canonical platform domains.

Memory stores durable, approved distilled facts. Knowledge, Blackboard, and session
history remain the systems of record for their own content. Memory keeps typed
references to those records rather than copying their source payloads.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit


class MemorySourceDomain(str, Enum):
    KNOWLEDGE = "knowledge"
    BLACKBOARD = "blackboard"
    SESSION = "session"


@dataclass(frozen=True)
class MemorySourceReference:
    """Stable pointer to a canonical record owned by another domain."""

    domain: MemorySourceDomain
    tenant_id: str
    record_id: str
    version: int
    approved: bool = True

    def __post_init__(self) -> None:
        for name in ("tenant_id", "record_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
            if "/" in value or "@" in value:
                raise ValueError(f"{name} contains a reserved reference character")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("source version must be a positive integer")
        if self.domain is MemorySourceDomain.BLACKBOARD and not self.approved:
            raise ValueError("memory may only cite approved Blackboard decisions")

    @property
    def uri(self) -> str:
        return f"{self.domain.value}://{self.tenant_id}/{self.record_id}@{self.version}"

    @classmethod
    def parse(cls, value: str, *, approved: bool = True) -> "MemorySourceReference":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("source reference must be a non-empty string")
        parsed = urlsplit(value)
        try:
            domain = MemorySourceDomain(parsed.scheme)
        except ValueError as exc:
            raise ValueError("source reference must target knowledge, blackboard, or session") from exc
        if parsed.query or parsed.fragment or not parsed.netloc or not parsed.path.startswith("/"):
            raise ValueError("source reference must contain tenant and record identity only")
        record_version = parsed.path[1:].rsplit("@", 1)
        if len(record_version) != 2:
            raise ValueError("source reference must pin an explicit version")
        record_id, raw_version = record_version
        if not raw_version.isdecimal():
            raise ValueError("source version must be a positive integer")
        return cls(domain, parsed.netloc, record_id, int(raw_version), approved)


class MemoryBoundaryPolicy:
    """Enforce one-way references and prevent domain responsibility overlap."""

    @staticmethod
    def validate_reference(value: str, *, approved: bool = True) -> MemorySourceReference:
        return MemorySourceReference.parse(value, approved=approved)

    @staticmethod
    def responsibility(domain: MemorySourceDomain) -> str:
        return {
            MemorySourceDomain.KNOWLEDGE: "Canonical documents, chunks, source versions, ACLs, and retrieval citations.",
            MemorySourceDomain.BLACKBOARD: "Transient expert collaboration, proposals, shared working state, and decisions.",
            MemorySourceDomain.SESSION: "Append-only conversation messages, turns, and session chronology.",
        }[domain]

    @staticmethod
    def memory_may_retain(domain: MemorySourceDomain) -> str:
        return {
            MemorySourceDomain.KNOWLEDGE: "A distilled durable fact plus a pinned document/chunk reference; never a copied document.",
            MemorySourceDomain.BLACKBOARD: "A durable outcome from an approved decision plus its versioned decision reference; never drafts.",
            MemorySourceDomain.SESSION: "A durable fact extracted from a turn plus a pinned message/turn reference; never a transcript.",
        }[domain]
