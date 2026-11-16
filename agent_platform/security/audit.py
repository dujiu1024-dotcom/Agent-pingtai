"""Append-only platform audit writer with bounded secret redaction."""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any, Protocol

from agent_platform.observability.redaction import redact_payload


class AuditWriter(Protocol):
    async def record(
        self, *, tenant_id: str, actor_id: str, action: str,
        subject_id: str, outcome: str = "succeeded",
        detail: Mapping[str, Any] | None = None,
    ) -> None: ...


class PostgresAuditWriter:
    """Write only redacted metadata to the append-only identity audit ledger."""

    _ACTION = re.compile(r"[a-z][a-z0-9_.-]{1,127}")
    _OUTCOMES = {"succeeded", "denied", "failed"}

    def __init__(self, connection_factory: Any) -> None:
        self._connect = connection_factory

    async def record(
        self, *, tenant_id: str, actor_id: str, action: str,
        subject_id: str, outcome: str = "succeeded",
        detail: Mapping[str, Any] | None = None,
    ) -> None:
        if not tenant_id.strip() or not actor_id.strip() or not subject_id.strip():
            raise ValueError("tenant_id, actor_id and subject_id are required")
        if not self._ACTION.fullmatch(action) or outcome not in self._OUTCOMES:
            raise ValueError("invalid audit action or outcome")
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
