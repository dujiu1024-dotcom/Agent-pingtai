"""Tenant-scoped human approval lifecycle for supervised tool execution."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable
from uuid import UUID, uuid4

from agent_platform.approval.policy import ActionRequest, RiskLevel


@dataclass(frozen=True)
class ApprovalRecord:
    approval_id: str
    tenant_id: str
    run_id: str | None
    action: str
    resource: str
    risk_level: int
    action_hash: str
    requested_by: str
    decided_by: str | None
    status: str
    expires_at: datetime


class ApprovalRepository:
    async def create(self, record: ApprovalRecord) -> None: ...
    async def get(self, tenant_id: str, approval_id: str) -> ApprovalRecord | None: ...
    async def list_pending(self, tenant_id: str, *, limit: int) -> list[ApprovalRecord]: ...
    async def decide(self, tenant_id: str, approval_id: str, *, approver_id: str,
                     approved: bool, now: datetime) -> bool: ...
    async def revoke(self, tenant_id: str, approval_id: str, *, actor_id: str, now: datetime, allow_admin: bool = False) -> bool: ...


class ApprovalService:
    def __init__(self, repository: ApprovalRepository, *,
                 clock: Callable[[], datetime] | None = None) -> None:
        self.repository = repository
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def request(self, *, tenant_id: str, actor_id: str, action: str,
                      resource: str, parameters: dict[str, Any],
                      risk_level: RiskLevel, run_id: str | None = None,
                      ttl: timedelta = timedelta(minutes=15)) -> ApprovalRecord:
        if risk_level < RiskLevel.EXTERNAL_OR_IRREVERSIBLE:
            raise ValueError("approval is only valid for high-risk actions")
        if ttl <= timedelta(0) or ttl > timedelta(hours=24):
            raise ValueError("approval TTL must be greater than zero and at most 24 hours")
        if not tenant_id or not actor_id or not action or not resource:
            raise ValueError("tenant, actor, action and resource are required")
        request = ActionRequest(action=action, resource=resource,
                                parameters=parameters, actor_id=actor_id)
        record = ApprovalRecord(
            approval_id=str(uuid4()), tenant_id=tenant_id, run_id=run_id,
            action=action, resource=resource, risk_level=int(risk_level),
            action_hash=request.digest(), requested_by=actor_id,
            decided_by=None, status="pending", expires_at=self.clock() + ttl,
        )
        await self.repository.create(record)
        return record

    async def pending(self, *, tenant_id: str, limit: int = 100) -> list[ApprovalRecord]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        return await self.repository.list_pending(tenant_id, limit=limit)

    async def revoke(self, *, tenant_id: str, approval_id: str,
                     actor_id: str, allow_admin: bool = False) -> bool:
        if not actor_id:
            raise ValueError("actor is required")
        record = await self.repository.get(tenant_id, approval_id)
        if record is None or record.status != "pending" or record.expires_at <= self.clock():
            return False
        if record.requested_by != actor_id and not allow_admin:
            raise PermissionError("only the requester or an authorized approver may revoke")
        return await self.repository.revoke(
            tenant_id, approval_id, actor_id=actor_id, now=self.clock(), allow_admin=allow_admin,
        )

    async def decide(self, *, tenant_id: str, approval_id: str,
                     approver_id: str, approved: bool) -> bool:
        if not approver_id:
            raise ValueError("approver is required")
        record = await self.repository.get(tenant_id, approval_id)
        if record is None or record.status != "pending":
            return False
        if record.requested_by == approver_id:
            raise PermissionError("requester cannot approve their own action")
        if record.expires_at <= self.clock():
            return False
        return await self.repository.decide(
            tenant_id, approval_id, approver_id=approver_id,
            approved=approved, now=self.clock(),
        )


class PostgresApprovalRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    @staticmethod
    def _record(row: dict[str, Any]) -> ApprovalRecord:
        return ApprovalRecord(
            approval_id=str(row["approval_id"]), tenant_id=row["tenant_id"],
            run_id=str(row["run_id"]) if row["run_id"] else None,
            action=row["action"], resource=row["resource"],
            risk_level=row["risk_level"], action_hash=row["action_hash"],
            requested_by=row["requested_by"], decided_by=row["decided_by"],
            status=row["status"], expires_at=row["expires_at"],
        )

    async def create(self, record: ApprovalRecord) -> None:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                await connection.execute(
                    """INSERT INTO platform_approvals
                    (tenant_id,approval_id,run_id,action,resource,risk_level,action_hash,
                     requested_by,status,expires_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s)""",
                    (record.tenant_id, UUID(record.approval_id),
                     UUID(record.run_id) if record.run_id else None,
                     record.action, record.resource, record.risk_level,
                     record.action_hash, record.requested_by, record.expires_at),
                )
                await connection.execute(
                    """INSERT INTO platform_tool_audit
                       (tenant_id,actor_id,action,resource,action_digest,outcome,detail,run_id)
                       VALUES (%s,%s,'approval.request',%s,%s,'started',%s,%s)""",
                    (record.tenant_id,record.requested_by,
                     f"approval:{record.approval_id}",record.action_hash,
                     f"risk_level={record.risk_level};action={record.action}",
                     record.run_id),
                )

    async def get(self, tenant_id: str, approval_id: str) -> ApprovalRecord | None:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                "SELECT * FROM platform_approvals WHERE tenant_id=%s AND approval_id=%s",
                (tenant_id, UUID(approval_id)),
            )
            row = await cursor.fetchone()
        return self._record(row) if row else None

    async def list_pending(self, tenant_id: str, *, limit: int) -> list[ApprovalRecord]:
        connection = await self._connect()
        async with connection:
            cursor = await connection.execute(
                """SELECT * FROM platform_approvals
                WHERE tenant_id=%s AND status='pending' AND expires_at>now()
                ORDER BY created_at ASC LIMIT %s""", (tenant_id, limit),
            )
            rows = await cursor.fetchall()
        return [self._record(row) for row in rows]

    async def revoke(self, tenant_id: str, approval_id: str, *,
                     actor_id: str, now: datetime, allow_admin: bool = False) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_approvals
                       SET status='revoked',revoked_by=%s,revoked_at=%s
                       WHERE tenant_id=%s AND approval_id=%s
                         AND status='pending' AND expires_at>%s
                         AND (requested_by=%s OR %s)
                       RETURNING action,resource,action_hash,run_id""",
                    (actor_id,now,tenant_id,UUID(approval_id),now,actor_id,allow_admin),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                await connection.execute(
                    """INSERT INTO platform_tool_audit
                       (tenant_id,actor_id,action,resource,action_digest,outcome,detail,run_id)
                       VALUES (%s,%s,'approval.revoke',%s,%s,'succeeded',%s,%s)""",
                    (tenant_id,actor_id,f"approval:{approval_id}",row["action_hash"],
                     f"revoked;action={row['action']};resource={row['resource']}",
                     str(row["run_id"]) if row["run_id"] else None),
                )
                return True

    async def decide(self, tenant_id: str, approval_id: str, *, approver_id: str,
                     approved: bool, now: datetime) -> bool:
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                cursor = await connection.execute(
                    """UPDATE platform_approvals
                    SET status=%s, decided_by=%s, decided_at=%s
                    WHERE tenant_id=%s AND approval_id=%s
                      AND status='pending' AND expires_at>%s
                      AND requested_by<>%s
                    RETURNING approval_id,action,resource,action_hash,run_id""",
                    ("approved" if approved else "rejected", approver_id, now,
                     tenant_id, UUID(approval_id), now, approver_id),
                )
                row = await cursor.fetchone()
                if row is None:
                    return False
                await connection.execute(
                    """INSERT INTO platform_tool_audit
                       (tenant_id,actor_id,action,resource,action_digest,outcome,detail,run_id)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (tenant_id,approver_id,
                     "approval.approve" if approved else "approval.reject",
                     f"approval:{approval_id}",row["action_hash"],
                     "succeeded" if approved else "denied",
                     f"decision={'approved' if approved else 'rejected'};action={row['action']};resource={row['resource']}",
                     str(row["run_id"]) if row["run_id"] else None),
                )
                return True
