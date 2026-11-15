"""PostgreSQL-backed approval consumption and tool audit sink."""

from __future__ import annotations

from typing import Any, Callable


ConnectionFactory = Callable[[], Any]


class PostgresApprovalVerifier:
    def __init__(self, connection_factory: ConnectionFactory) -> None:
        self._connection_factory = connection_factory

    async def consume(
        self, *, tenant_id: str, approval_id: str, action_digest: str
    ) -> bool:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """
                    UPDATE platform_approvals
                    SET status = 'consumed', decided_at = COALESCE(decided_at, now())
                    WHERE tenant_id = %s
                      AND approval_id = %s
                      AND action_hash = %s
                      AND status = 'approved'
                      AND expires_at > now()
                    RETURNING approval_id
                    """,
                    (tenant_id, approval_id, action_digest),
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
        detail: str = "",
    ) -> None:
        connection = await self._connection_factory()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """
                    INSERT INTO platform_tool_audit (
                        tenant_id, actor_id, action, resource,
                        action_digest, outcome, detail
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        tenant_id, actor_id, action, resource,
                        action_digest, outcome, detail,
                    ),
                )
