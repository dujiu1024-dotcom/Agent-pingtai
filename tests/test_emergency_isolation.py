import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.security.emergency_isolation import (
    EmergencyIsolationError, IsolationRecord, IsolationScope,
    IsolationSnapshot, RuntimeSafetyShell, matching_isolation,
)
from agent_platform.tools.gateway import ToolExecutionContext

NOW = datetime(2026, 11, 17, 10, tzinfo=timezone.utc)


class State:
    def __init__(self, snapshot=None, error=None):
        self.snapshot, self.error = snapshot, error
    async def load(self):
        if self.error:
            raise self.error
        return self.snapshot


class Audit:
    def __init__(self, error=None):
        self.events, self.error = [], error
    async def write(self, event):
        if self.error:
            raise self.error
        self.events.append(dict(event))


def context(tenant="t1", run="r1"):
    return ToolExecutionContext(
        tenant_id=tenant, actor_id="a1", actor_roles=("operator",),
        run_id=run, agent_id="agent", tool_name="shell.exec", resource="run:" + run,
    )


class EmergencyIsolationTests(unittest.IsolatedAsyncioTestCase):
    def snapshot(self, records=(), observed=NOW):
        return IsolationSnapshot(tuple(records), observed, "v1")

    async def test_global_tenant_and_run_scopes_block_only_matching_execution(self):
        for scope, target, ctx in (
            (IsolationScope.GLOBAL, "*", context("t2", "r2")),
            (IsolationScope.TENANT, "t1", context("t1", "r2")),
            (IsolationScope.RUN, "r1", context("t2", "r1")),
        ):
            with self.subTest(scope=scope):
                record = IsolationRecord(scope, target, "incident", NOW, incident_id="inc-1")
                called = []
                async def handler(args, ctx):
                    called.append(True)
                audit = Audit()
                shell = RuntimeSafetyShell(handler, State(self.snapshot([record])), audit, clock=lambda: NOW)
                with self.assertRaises(EmergencyIsolationError):
                    await shell({}, ctx)
                self.assertEqual(called, [])
                self.assertEqual(audit.events[0]["event"], "runtime.execution_blocked")

    async def test_expired_and_nonmatching_records_allow_execution(self):
        expired = IsolationRecord(IsolationScope.RUN, "r1", "expired", NOW - timedelta(hours=2), NOW - timedelta(hours=1))
        called = []
        async def handler(args, ctx):
            called.append(True)
            return "ok"
        audit = Audit()
        shell = RuntimeSafetyShell(handler, State(self.snapshot([expired])), audit, clock=lambda: NOW)
        self.assertEqual(await shell({}, context()), "ok")
        self.assertEqual(called, [True])
        self.assertEqual([e["event"] for e in audit.events], [
            "runtime.execution_authorized", "runtime.execution_completed"
        ])

    async def test_stale_state_and_control_plane_failure_fail_closed(self):
        async def handler(args, ctx):
            self.fail("must not execute")
        for state in (
            State(self.snapshot(observed=NOW - timedelta(minutes=1))),
            State(error=OSError("unavailable")),
        ):
            shell = RuntimeSafetyShell(handler, state, Audit(), clock=lambda: NOW)
            with self.assertRaises(EmergencyIsolationError):
                await shell({}, context())

    async def test_audit_failure_blocks_execution(self):
        called = []
        async def handler(args, ctx):
            called.append(True)
        shell = RuntimeSafetyShell(
            handler, State(self.snapshot()), Audit(error=OSError("audit down")), clock=lambda: NOW
        )
        with self.assertRaises(EmergencyIsolationError):
            await shell({}, context())
        self.assertEqual(called, [])

    async def test_isolation_audit_failure_still_blocks_execution(self):
        record = IsolationRecord(IsolationScope.RUN, "r1", "incident", NOW)
        async def handler(args, ctx):
            self.fail("must not execute")
        shell = RuntimeSafetyShell(
            handler, State(self.snapshot([record])), Audit(error=OSError("audit down")), clock=lambda: NOW
        )
        with self.assertRaises(EmergencyIsolationError):
            await shell({}, context())

    async def test_rejects_naive_or_invalid_expiry_timestamps(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            IsolationRecord(IsolationScope.RUN, "r", "reason", datetime(2026, 1, 1))
        with self.assertRaisesRegex(ValueError, "after activation"):
            IsolationRecord(IsolationScope.RUN, "r", "reason", NOW, NOW)

    async def test_matching_prefers_run_scope(self):
        records = [
            IsolationRecord(IsolationScope.GLOBAL, "*", "global", NOW),
            IsolationRecord(IsolationScope.TENANT, "t1", "tenant", NOW),
            IsolationRecord(IsolationScope.RUN, "r1", "run", NOW),
        ]
        self.assertEqual(matching_isolation(self.snapshot(records), context(), NOW).scope, IsolationScope.RUN)


if __name__ == "__main__":
    unittest.main()
