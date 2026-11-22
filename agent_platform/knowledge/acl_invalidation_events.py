"""Typed, version-stable contract for Knowledge ACL invalidation events."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Mapping
from uuid import UUID


class ACLInvalidationResourceType(str, Enum):
    DOCUMENT = "document"
    CHUNK = "chunk"


class ACLInvalidationReason(str, Enum):
    ACL_CHANGED = "acl_changed"
    DELETED = "deleted"
    UPDATED = "updated"


@dataclass(frozen=True, slots=True)
class KnowledgeACLInvalidationEvent:
    """Immutable event envelope shared by the outbox dispatcher and subscribers."""

    event_id: int
    tenant_id: str
    resource_type: ACLInvalidationResourceType
    resource_id: UUID
    reason: ACLInvalidationReason
    created_at: datetime

    def __post_init__(self) -> None:
        if isinstance(self.event_id, bool) or not isinstance(self.event_id, int) or self.event_id < 1:
            raise ValueError("event_id must be a positive integer")
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id must be a non-empty string")
        if len(self.tenant_id.strip()) > 255:
            raise ValueError("tenant_id must be at most 255 characters")
        if not isinstance(self.resource_type, ACLInvalidationResourceType):
            raise ValueError("resource_type must be document or chunk")
        if not isinstance(self.resource_id, UUID):
            raise ValueError("resource_id must be a UUID")
        if not isinstance(self.reason, ACLInvalidationReason):
            raise ValueError("reason must be acl_changed, deleted, or updated")
        if not isinstance(self.created_at, datetime) or self.created_at.tzinfo is None:
            raise ValueError("created_at must be a timezone-aware datetime")
        if self.created_at.utcoffset() is None:
            raise ValueError("created_at must be a timezone-aware datetime")
        object.__setattr__(self, "tenant_id", self.tenant_id.strip())

    def to_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible payload."""
        return {
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "resource_type": self.resource_type.value,
            "resource_id": str(self.resource_id),
            "reason": self.reason.value,
            "created_at": self.created_at.isoformat(),
        }

    def to_json(self) -> str:
        """Serialize deterministically for PostgreSQL NOTIFY and other transports."""
        return json.dumps(
            self.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "KnowledgeACLInvalidationEvent":
        if not isinstance(payload, Mapping):
            raise ValueError("event payload must be an object")
        expected = {
            "event_id", "tenant_id", "resource_type", "resource_id", "reason", "created_at",
        }
        if set(payload) != expected:
            missing = sorted(expected - set(payload))
            extra = sorted(set(payload) - expected)
            raise ValueError(f"invalid event fields (missing={missing}, extra={extra})")
        timestamp = payload["created_at"]
        if not isinstance(timestamp, str) or not timestamp.strip():
            raise ValueError("created_at must be an ISO-8601 string")
        try:
            created_at = datetime.fromisoformat(
                timestamp[:-1] + "+00:00" if timestamp.endswith("Z") else timestamp
            )
            resource_id = UUID(str(payload["resource_id"]))
            resource_type = ACLInvalidationResourceType(payload["resource_type"])
            reason = ACLInvalidationReason(payload["reason"])
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid ACL invalidation event value") from exc
        return cls(
            event_id=payload["event_id"],
            tenant_id=payload["tenant_id"],
            resource_type=resource_type,
            resource_id=resource_id,
            reason=reason,
            created_at=created_at,
        )

    @classmethod
    def from_json(cls, value: str) -> "KnowledgeACLInvalidationEvent":
        try:
            payload = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("event must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ValueError("event JSON must contain an object")
        return cls.from_dict(payload)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "KnowledgeACLInvalidationEvent":
        """Build from a database row without exposing a database dependency."""
        created_at = record.get("created_at")
        if isinstance(created_at, str):
            try:
                created_at = datetime.fromisoformat(
                    created_at[:-1] + "+00:00" if created_at.endswith("Z") else created_at
                )
            except ValueError as exc:
                raise ValueError("created_at must be a valid ISO-8601 timestamp") from exc
        try:
            return cls(
                event_id=record["event_id"],
                tenant_id=record["tenant_id"],
                resource_type=ACLInvalidationResourceType(record["resource_type"]),
                resource_id=UUID(str(record["resource_id"])),
                reason=ACLInvalidationReason(record["reason"]),
                created_at=created_at,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid ACL invalidation record") from exc
