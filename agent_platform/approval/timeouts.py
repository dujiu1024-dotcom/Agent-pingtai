"""Approval timeout orchestration: safe downgrade, deny, or emergency Run interrupt.

The policy resolver is trusted server configuration. An expired approval never
becomes an approval; downgrade callbacks must independently authorize a
strictly lower-risk fallback before execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Mapping

from agent_platform.approval.policy import ActionRequest, RiskLevel


class TimeoutDisposition(str, Enum):
    DENY = "deny"
    DOWNGRADE = "downgrade"
    INTERRUPT = "interrupt"


@dataclass(frozen=True)
class ApprovalTimeout:
    approval_id: str
    tenant_id: str
    run_id: str | None
    action: str
    resource: str
    risk_level: int
    action_hash: str
    requested_by: str
    expires_at: datetime


@dataclass(frozen=True)
class FallbackAction:
    action: str
    resource: str
    parameters: Mapping[str, Any]
    risk_level: RiskLevel


@dataclass(frozen=True)
class ApprovalTimeoutRule:
    disposition: TimeoutDisposition
    fallback: FallbackAction | None = None


@dataclass(frozen=True)
class TimeoutResult:
    approval_id: str
    disposition: str
    outcome: str


class ApprovalTimeoutRepository:
    async def expire_pending(
        self, *, tenant_id: str | None, now: datetime, limit: int,
    ) -> list[ApprovalTimeout]: ...


RuleResolver = Callable[[ApprovalTimeout], ApprovalTimeoutRule]
FallbackAuthorizer = Callable[[ApprovalTimeout, ActionRequest, RiskLevel], Awaitable[bool]]
FallbackExecutor = Callable[[ApprovalTimeout, ActionRequest], Awaitable[None]]
RunInterrupter = Callable[[str, str], Awaitable[Any]]


class ApprovalTimeoutCoordinator:
    """Process expired approvals with explicit fail-closed timeout behavior."""

    def __init__(
        self,
        repository: ApprovalTimeoutRepository,
        *,
        resolve_rule: RuleResolver,
        audit_sink: Any,
        authorize_fallback: FallbackAuthorizer | None = None,
        execute_fallback: FallbackExecutor | None = None,
        interrupt_run: RunInterrupter | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not callable(getattr(audit_sink, "record", None)):
            raise TypeError("approval timeout coordinator requires an audit sink")
        self.repository = repository
        self.resolve_rule = resolve_rule
        self.audit_sink = audit_sink
        self.authorize_fallback = authorize_fallback
        self.execute_fallback = execute_fallback
        self.interrupt_run = interrupt_run
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def process(self, *, tenant_id: str | None = None, limit: int = 100) -> list[TimeoutResult]:
        if tenant_id is not None and (not isinstance(tenant_id, str) or not tenant_id.strip()):
            raise ValueError("tenant_id must be a non-empty string when supplied")
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        now = self.clock()
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("timeout coordinator clock must return an aware datetime")
        expired = await self.repository.expire_pending(tenant_id=tenant_id, now=now, limit=limit)
        results: list[TimeoutResult] = []
        for approval in expired:
            rule = self.resolve_rule(approval)
            outcome = await self._apply(approval, rule)
            results.append(TimeoutResult(approval.approval_id, rule.disposition.value, outcome))
        return results

    async def _apply(self, approval: ApprovalTimeout, rule: ApprovalTimeoutRule) -> str:
        if rule.disposition is TimeoutDisposition.DENY:
            await self._audit(approval, "approval.timeout.denied", "denied")
            return "denied"

        if rule.disposition is TimeoutDisposition.INTERRUPT:
            if not approval.run_id or self.interrupt_run is None:
                await self._audit(approval, "approval.timeout.interrupt", "failed")
                return "failed_closed"
            try:
                await self.interrupt_run(approval.tenant_id, approval.run_id)
            except Exception:
                await self._audit(approval, "approval.timeout.interrupt", "failed")
                return "failed_closed"
            await self._audit(approval, "approval.timeout.interrupt", "succeeded")
            return "interrupted"

        if rule.disposition is not TimeoutDisposition.DOWNGRADE or rule.fallback is None:
            await self._audit(approval, "approval.timeout.downgrade", "failed")
            return "failed_closed"
        fallback = rule.fallback
        if (
            not fallback.action.strip()
            or not fallback.resource.strip()
            or fallback.risk_level >= RiskLevel.EXTERNAL_OR_IRREVERSIBLE
            or int(fallback.risk_level) >= approval.risk_level
            or self.authorize_fallback is None
            or self.execute_fallback is None
        ):
            await self._audit(approval, "approval.timeout.downgrade", "failed")
            return "failed_closed"
        request = ActionRequest(
            action=fallback.action,
            resource=fallback.resource,
            parameters=fallback.parameters,
            actor_id=approval.requested_by,
        )
        try:
            allowed = await self.authorize_fallback(approval, request, fallback.risk_level)
            if allowed is not True:
                await self._audit(approval, "approval.timeout.downgrade", "denied")
                return "failed_closed"
            await self.execute_fallback(approval, request)
        except Exception:
            await self._audit(approval, "approval.timeout.downgrade", "failed")
            return "failed_closed"
        await self._audit(approval, "approval.timeout.downgrade", "succeeded")
        return "downgraded"

    async def _audit(self, approval: ApprovalTimeout, action: str, outcome: str) -> None:
        # The audit sink is mandatory. An audit outage propagates to the worker
        # so the caller can alert/retry; it must never be silently swallowed.
        await self.audit_sink.record(
            tenant_id=approval.tenant_id,
            actor_id="platform-system:approval-timeout",
            action=action,
            resource=f"approval:{approval.approval_id}",
            action_digest=approval.action_hash,
            outcome=outcome,
            run_id=approval.run_id,
            detail=f"expired_action={approval.action};disposition={action.rsplit('.', 1)[-1]}",
        )


class PostgresApprovalTimeoutRepository:
    """Atomically expire a bounded batch and write its audit events."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connect = connection_factory

    async def expire_pending(
        self, *, tenant_id: str | None, now: datetime, limit: int,
    ) -> list[ApprovalTimeout]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        connection = await self._connect()
        async with connection:
            async with connection.transaction():
                scope = "AND tenant_id=%s" if tenant_id is not None else ""
                cursor = await connection.execute(
                    f"""WITH due AS (
                           SELECT tenant_id, approval_id
                           FROM platform_approvals
                           WHERE status='pending' AND expires_at<=%s {scope}
                           ORDER BY expires_at, tenant_id, approval_id
                           LIMIT %s
                           FOR UPDATE SKIP LOCKED
                       )
                       UPDATE platform_approvals AS a
                       SET status='expired', decided_at=%s
                       FROM due
                       WHERE a.tenant_id=due.tenant_id AND a.approval_id=due.approval_id
                       RETURNING a.approval_id,a.tenant_id,a.run_id,a.action,a.resource,
                                 a.risk_level,a.action_hash,a.requested_by,a.expires_at""",
                    ((now, tenant_id, limit, now) if tenant_id is not None else (now, limit, now)),
                )
                rows = await cursor.fetchall()
                for row in rows:
                    await connection.execute(
                        """INSERT INTO platform_tool_audit
                           (tenant_id,actor_id,action,resource,action_digest,outcome,detail,run_id)
                           VALUES (%s,'platform-system:approval-timeout',
                                   'approval.timeout.detected',%s,%s,'failed',%s,%s)""",
                        (row["tenant_id"], f"approval:{row['approval_id']}", row["action_hash"],
                         f"expired_action={row['action']};risk_level={row['risk_level']}",
                         str(row["run_id"]) if row["run_id"] else None),
                    )
        return [ApprovalTimeout(
            approval_id=str(row["approval_id"]), tenant_id=row["tenant_id"],
            run_id=str(row["run_id"]) if row["run_id"] else None,
            action=row["action"], resource=row["resource"], risk_level=row["risk_level"],
            action_hash=row["action_hash"], requested_by=row["requested_by"],
            expires_at=row["expires_at"],
        ) for row in rows]
