from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.approval.policy import RiskLevel
from agent_platform.approval.timeouts import (
    ApprovalTimeout,
    ApprovalTimeoutCoordinator,
    ApprovalTimeoutRule,
    FallbackAction,
    TimeoutDisposition,
)


class Repository:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def expire_pending(self, *, tenant_id, now, limit):
        self.calls.append((tenant_id, now, limit))
        return [row for row in self.rows if tenant_id is None or row.tenant_id == tenant_id][:limit]


class Audit:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    async def record(self, **event):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


def approval(*, run_id="run-a", risk=2):
    return ApprovalTimeout(
        approval_id="approval-a", tenant_id="tenant-a", run_id=run_id,
        action="external.transfer", resource="account:1", risk_level=risk,
        action_hash="a" * 64, requested_by="requester",
        expires_at=datetime(2026, 11, 17, tzinfo=timezone.utc) - timedelta(seconds=1),
    )


class ApprovalTimeoutCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_deny_timeout_never_executes_side_effect(self):
        row = approval()
        repo, audit = Repository([row]), Audit()
        called = []
        coordinator = ApprovalTimeoutCoordinator(
            repo, resolve_rule=lambda _: ApprovalTimeoutRule(TimeoutDisposition.DENY),
            audit_sink=audit,
            execute_fallback=lambda *_: _append(called, "fallback"),
        )
        result = await coordinator.process(tenant_id="tenant-a")
        self.assertEqual(result[0].outcome, "denied")
        self.assertEqual(called, [])
        self.assertEqual(audit.events[0]["action"], "approval.timeout.denied")

    async def test_downgrade_requires_lower_risk_and_trusted_authorization(self):
        row = approval()
        repo, audit, executed = Repository([row]), Audit(), []
        async def authorize(approval_record, request, risk):
            return request.action == "document.preview" and risk == RiskLevel.READ_ONLY
        async def execute(approval_record, request):
            executed.append((approval_record.approval_id, request.action))
        coordinator = ApprovalTimeoutCoordinator(
            repo,
            resolve_rule=lambda _: ApprovalTimeoutRule(
                TimeoutDisposition.DOWNGRADE,
                FallbackAction("document.preview", "account:1", {"format": "summary"}, RiskLevel.READ_ONLY),
            ),
            audit_sink=audit, authorize_fallback=authorize, execute_fallback=execute,
        )
        result = await coordinator.process(tenant_id="tenant-a")
        self.assertEqual(result[0].outcome, "downgraded")
        self.assertEqual(executed, [("approval-a", "document.preview")])
        self.assertEqual(audit.events[-1]["outcome"], "succeeded")

    async def test_equal_or_high_risk_fallback_fails_closed(self):
        row = approval()
        repo, audit, executed = Repository([row]), Audit(), []
        coordinator = ApprovalTimeoutCoordinator(
            repo,
            resolve_rule=lambda _: ApprovalTimeoutRule(
                TimeoutDisposition.DOWNGRADE,
                FallbackAction("external.transfer", "account:1", {}, RiskLevel.EXTERNAL_OR_IRREVERSIBLE),
            ),
            audit_sink=audit,
            authorize_fallback=lambda *_: _true(),
            execute_fallback=lambda *_: _append(executed, "executed"),
        )
        result = await coordinator.process(tenant_id="tenant-a")
        self.assertEqual(result[0].outcome, "failed_closed")
        self.assertEqual(executed, [])

    async def test_emergency_interrupt_requires_run_and_uses_tenant_scope(self):
        row = approval()
        repo, audit, interrupted = Repository([row]), Audit(), []
        async def interrupt(tenant_id, run_id):
            interrupted.append((tenant_id, run_id))
        coordinator = ApprovalTimeoutCoordinator(
            repo, resolve_rule=lambda _: ApprovalTimeoutRule(TimeoutDisposition.INTERRUPT),
            audit_sink=audit, interrupt_run=interrupt,
        )
        result = await coordinator.process()
        self.assertEqual(result[0].outcome, "interrupted")
        self.assertEqual(interrupted, [("tenant-a", "run-a")])

    async def test_interrupt_failure_is_fail_closed_and_audited(self):
        row = approval()
        repo, audit = Repository([row]), Audit()
        async def interrupt(*_):
            raise RuntimeError("interrupt unavailable")
        coordinator = ApprovalTimeoutCoordinator(
            repo, resolve_rule=lambda _: ApprovalTimeoutRule(TimeoutDisposition.INTERRUPT),
            audit_sink=audit, interrupt_run=interrupt,
        )
        result = await coordinator.process()
        self.assertEqual(result[0].outcome, "failed_closed")
        self.assertEqual(audit.events[-1]["outcome"], "failed")

    async def test_audit_failure_propagates_for_worker_retry(self):
        row = approval()
        coordinator = ApprovalTimeoutCoordinator(
            Repository([row]), resolve_rule=lambda _: ApprovalTimeoutRule(TimeoutDisposition.DENY),
            audit_sink=Audit(fail=True),
        )
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await coordinator.process()

    async def test_invalid_limit_and_naive_clock_are_rejected(self):
        coordinator = ApprovalTimeoutCoordinator(
            Repository([]), resolve_rule=lambda _: ApprovalTimeoutRule(TimeoutDisposition.DENY),
            audit_sink=Audit(), clock=lambda: datetime(2026, 11, 17),
        )
        with self.assertRaises(ValueError):
            await coordinator.process(limit=0)
        with self.assertRaises(ValueError):
            await coordinator.process()


async def _append(target, value):
    target.append(value)


async def _true():
    return True


if __name__ == "__main__":
    unittest.main()
