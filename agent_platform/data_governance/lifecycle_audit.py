"""Append-only, tenant-scoped audit evidence for migration and data governance operations.

Only identifiers, bounded scope metadata and evidence digests are persisted; callers
must never pass source records, credentials, prompts or exported payloads as evidence.
"""
# ruff: noqa: I001 -- keep the standard-library import block explicit.
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol
from uuid import UUID, uuid4


_ACTION = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
_OUTCOMES = {"started", "succeeded", "failed", "denied", "retry_scheduled"}
_SCOPE_KEYS = {"resource_type", "resource_id", "batch_id", "request_id", "source", "destination", "operation"}
_FAILURE_CODE = re.compile(r"[A-Z][A-Z0-9_.-]{1,63}")


def evidence_digest(evidence: bytes | str | Mapping[str, Any] | None) -> str | None:
    """Return a stable SHA-256 digest without persisting the evidence itself."""
    if evidence is None:
        return None
    if isinstance(evidence, Mapping):
        raw = json.dumps(dict(evidence), sort_keys=True, separators=(",", ":"),
                         ensure_ascii=False, allow_nan=False).encode("utf-8")
    elif isinstance(evidence, str):
        raw = evidence.encode("utf-8")
    elif isinstance(evidence, bytes):
        raw = evidence
    else:
        raise ValueError("evidence must be bytes, text, mapping or None")
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True)
class LifecycleAuditEvent:
    tenant_id: str
    actor_id: str
    action: str
    outcome: str
    scope: Mapping[str, str]
    correlation_id: str
    attempt: int = 1
    failure_code: str | None = None
    evidence_sha256: str | None = None
    event_id: str = ""
    occurred_at: datetime | None = None

    def __post_init__(self) -> None:
        for name, value in (("tenant_id", self.tenant_id), ("actor_id", self.actor_id),
                            ("correlation_id", self.correlation_id)):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{name} must be a non-empty string of at most 256 characters")
        if not isinstance(self.action, str) or not _ACTION.fullmatch(self.action):
            raise ValueError("invalid audit action")
        if self.outcome not in _OUTCOMES:
            raise ValueError("invalid audit outcome")
        if isinstance(self.attempt, bool) or not isinstance(self.attempt, int) or self.attempt < 1:
            raise ValueError("attempt must be a positive integer")
        if self.outcome == "failed" and not self.failure_code:
            raise ValueError("failed audit events require failure_code")
        if self.failure_code is not None and (
            not isinstance(self.failure_code, str) or not _FAILURE_CODE.fullmatch(self.failure_code)
        ):
            raise ValueError("invalid failure_code")
        if self.outcome != "failed" and self.failure_code is not None:
            raise ValueError("failure_code is only valid for failed events")
        if not isinstance(self.scope, Mapping) or not self.scope:
            raise ValueError("scope must be a non-empty mapping")
        if set(self.scope) - _SCOPE_KEYS:
            raise ValueError("scope contains unsupported keys")
        for key, value in self.scope.items():
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"scope.{key} must be a non-empty string of at most 256 characters")
        if self.evidence_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.evidence_sha256):
            raise ValueError("evidence_sha256 must be a lowercase SHA-256 digest")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("occurred_at must be timezone-aware")


class LifecycleAuditWriter(Protocol):
    async def append(self, event: LifecycleAuditEvent) -> None: ...


class LifecycleAuditReader(Protocol):
    async def list_events(self, *, tenant_id: str, limit: int = 100,
                          correlation_id: str | None = None) -> list[dict[str, Any]]: ...


class LifecycleAuditService:
    """Create linked attempt/result records for migration and governance actions."""

    def __init__(self, writer: LifecycleAuditWriter) -> None:
        self._writer = writer

    async def record(self, *, tenant_id: str, actor_id: str, action: str,
                     outcome: str, scope: Mapping[str, str],
                     correlation_id: str | None = None, attempt: int = 1,
                     failure_code: str | None = None,
                     evidence: bytes | str | Mapping[str, Any] | None = None) -> LifecycleAuditEvent:
        event = LifecycleAuditEvent(
            tenant_id=tenant_id, actor_id=actor_id, action=action, outcome=outcome,
            scope=dict(scope), correlation_id=correlation_id or str(uuid4()),
            attempt=attempt, failure_code=failure_code,
            evidence_sha256=evidence_digest(evidence), event_id=str(uuid4()),
            occurred_at=datetime.now(timezone.utc),
        )
        await self._writer.append(event)
        return event

    async def record_failure_and_retry(self, *, tenant_id: str, actor_id: str,
                                       action: str, scope: Mapping[str, str],
                                       correlation_id: str, attempt: int,
                                       failure_code: str,
                                       evidence: bytes | str | Mapping[str, Any] | None = None) -> tuple[LifecycleAuditEvent, LifecycleAuditEvent]:
        failed = await self.record(
            tenant_id=tenant_id, actor_id=actor_id, action=action, outcome="failed",
            scope=scope, correlation_id=correlation_id, attempt=attempt,
            failure_code=failure_code, evidence=evidence,
        )
        retry = await self.record(
            tenant_id=tenant_id, actor_id=actor_id, action=action, outcome="retry_scheduled",
            scope=scope, correlation_id=correlation_id, attempt=attempt + 1,
            evidence={"previous_failure_event": failed.event_id},
        )
        return failed, retry


class PostgresLifecycleAuditRepository:
    """Append-only PostgreSQL writer and tenant-scoped evidence reader."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def append(self, event: LifecycleAuditEvent) -> None:
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_data_lifecycle_audit
                   (event_id,tenant_id,actor_id,action,outcome,scope,correlation_id,
                    attempt,failure_code,evidence_sha256,occurred_at)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,%s,%s)""",
                (UUID(event.event_id), event.tenant_id, event.actor_id, event.action,
                 event.outcome, json.dumps(dict(event.scope), sort_keys=True),
                 UUID(event.correlation_id), event.attempt, event.failure_code,
                 event.evidence_sha256, event.occurred_at or datetime.now(timezone.utc)),
            )

    async def list_events(self, *, tenant_id: str, limit: int = 100,
                          correlation_id: str | None = None) -> list[dict[str, Any]]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        if correlation_id is not None:
            try:
                correlation_id = str(UUID(correlation_id))
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError("correlation_id must be a UUID") from exc
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT event_id,tenant_id,actor_id,action,outcome,scope,
                          correlation_id,attempt,failure_code,evidence_sha256,occurred_at
                   FROM platform_data_lifecycle_audit
                   WHERE tenant_id=%s AND (%s IS NULL OR correlation_id=%s)
                   ORDER BY occurred_at DESC,event_id DESC LIMIT %s""",
                (tenant_id, correlation_id, correlation_id, limit),
            )
            rows = await cursor.fetchall()
        return [dict(row) for row in rows]
