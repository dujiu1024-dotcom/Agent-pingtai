"""PostgreSQL-backed approval consumption and tool audit sink."""

from __future__ import annotations

from typing import Any, Callable
from uuid import UUID

from agent_platform.observability.redaction import redact_text

ConnectionFactory = Callable[[], Any]


class PostgresApprovalVerifier:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def consume(
        self, *, tenant_id: str, approval_id: str, action_digest: str,
        policy_version: str = "1",
    ) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE platform_approvals
                    SET status = 'consumed', consumed_at = now()
                    WHERE tenant_id = %s
                      AND approval_id = %s
                      AND action_hash = %s
                      AND policy_version = %s
                      AND status = 'approved'
                      AND expires_at > now()
                    RETURNING approval_id
                    """,
                    (tenant_id, UUID(approval_id), action_digest, policy_version),
                )
                return await cursor.fetchone() is not None


class PostgresAuditSink:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def record(
        self,
        *,
        tenant_id: str,
        actor_id: str,
        action: str,
        resource: str,
        action_digest: str,
        outcome: str,
        run_id: str | None = None,
        agent_id: str | None = None,
        detail: str = "",
    ) -> None:
        # platform_tool_audit has a deliberately small CHECK constraint:
        # denied / started / succeeded / failed. Preserve richer lifecycle
        # semantics in detail while mapping them to the durable DB vocabulary.
        outcome_aliases = {
            "replayed": "succeeded",
            "reconciliation_started": "started",
            "reconciliation_completed": "succeeded",
        }
        persisted_outcome = outcome_aliases.get(outcome, outcome)
        if persisted_outcome not in {"denied", "started", "succeeded", "failed"}:
            raise ValueError("unsupported platform tool audit outcome")
        persisted_detail = detail
        if persisted_outcome != outcome:
            persisted_detail = f"event={outcome}; {detail}" if detail else f"event={outcome}"
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO platform_tool_audit (
                        tenant_id, actor_id, action, resource,
                        action_digest, outcome, run_id, agent_id, detail
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id, actor_id, action, resource,
                        action_digest, persisted_outcome, run_id, agent_id,
                        redact_text(persisted_detail),
                    ),
                )


class PostgresAuditReader:
    """Read-only tenant-scoped access to append-only tool audit events."""

    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def list_events(
        self, *, tenant_id: str, limit: int = 100,
        actor_id: str | None = None, run_id: str | None = None,
    ) -> tuple[dict[str, Any], ...]:
        if not tenant_id.strip():
            raise ValueError("tenant_id is required")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        clauses = ["tenant_id=%s"]
        params: list[Any] = [tenant_id]
        if actor_id:
            clauses.append("actor_id=%s")
            params.append(actor_id)
        if run_id:
            clauses.append("run_id=%s")
            params.append(run_id)
        params.append(limit)
        connection = await self._connection_factory()
        async with connection:
            cursor = await connection.execute(
                """SELECT audit_id,actor_id,action,resource,action_digest,outcome,
                          run_id,agent_id,detail,created_at
                   FROM platform_tool_audit
                   WHERE """ + " AND ".join(clauses) + """
                   ORDER BY created_at DESC,audit_id DESC LIMIT %s""",
                tuple(params),
            )
            rows = await cursor.fetchall()
        return tuple(dict(row) for row in rows)
