"""Append-only platform audit writer with bounded secret redaction."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Protocol

from agent_platform.observability.redaction import redact_payload


class AuditWriter(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str | None, action: str,
        subject_id: str, outcome: str = "succeeded",
        detail: Mapping[str, Any] | None = None,
    ) -> None: ...


class PostgresAuditWriter:
    """Write only redacted metadata to the append-only identity audit ledger."""

    _ACTION = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
    _OUTCOMES = {"succeeded", "denied", "failed", "started", "skipped", "unknown"}

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def record(
        self, *, tenant_id: str, actor_id: str | None, action: str,
        subject_id: str, outcome: str = "succeeded",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        if (
            not isinstance(tenant_id, str) or not tenant_id.strip()
            or not isinstance(subject_id, str) or not subject_id.strip()
        ):
            raise ValueError("tenant_id and subject_id are required strings")
        if actor_id is not None and (
            not isinstance(actor_id, str) or not actor_id.strip()
        ):
            raise ValueError("actor_id must be a non-empty string when supplied")
        if not isinstance(action, str) or not self._ACTION.fullmatch(action):
            raise ValueError("invalid audit action")
        if not isinstance(outcome, str) or outcome not in self._OUTCOMES:
            raise ValueError("invalid audit outcome")
        if detail is not None and not isinstance(detail, Mapping):
            raise ValueError("audit detail must be a mapping")
        safe_detail = redact_payload(dict(detail or {}))
        payload = json.dumps(safe_detail, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
        connection = await self._connect()
        async with connection:
            await connection.execute(
                """INSERT INTO platform_identity_audit
                   (tenant_id,actor_id,action,subject_id,outcome,detail)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb)""",
                (tenant_id, actor_id, action, subject_id, outcome, payload),
            )


class PostgresIdentityAuditReader:
    """Tenant-scoped, read-only query adapter for the append-only identity ledger."""

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def list_events(
        self, *, tenant_id: str, limit: int = 100,
        actor_id: str | None = None, action: str | None = None,
    ) -> list[dict[str, Any]]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise ValueError("limit must be an integer between 1 and 500")
        if actor_id is not None and (
            not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256
        ):
            raise ValueError("actor_id must be a non-empty string of at most 256 characters")
        if action is not None and (
            not isinstance(action, str) or not PostgresAuditWriter._ACTION.fullmatch(action)
        ):
            raise ValueError("invalid audit action")
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT audit_id,tenant_id,actor_id,action,subject_id,
                          outcome,detail,created_at
                   FROM platform_identity_audit
                   WHERE tenant_id=%s
                     AND (%s::text IS NULL OR actor_id=%s)
                     AND (%s::text IS NULL OR action=%s)
                   ORDER BY created_at DESC,audit_id DESC
                   LIMIT %s""",
                (tenant_id, actor_id, actor_id, action, action, limit),
            )
            rows = await cursor.fetchall()
        return [
            {**dict(row), "detail": redact_payload(dict(row.get("detail") or {}))}
            for row in rows
        ]
