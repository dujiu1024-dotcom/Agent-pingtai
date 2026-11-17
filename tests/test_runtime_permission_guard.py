from __future__ import annotations

import unittest

from agent_platform.security.permission_runtime import (
    LeastPrivilegeRuntimeGuard,
    PermissionDecision,
    RuntimeCall,
    RuntimePermissionDenied,
)


class FakeAuthorizer:
    def __init__(self, decisions=None, error=None):
        self.decisions = list(decisions or [PermissionDecision(True, "v1")])
        self.error = error
        self.calls = []

    async def authorize(self, *, call):
        self.calls.append(call)
        if self.error:
            raise self.error
        return self.decisions.pop(0)


class FakeAudit:
    def __init__(self, fail=False):
        self.records = []
        self.fail = fail

    async def record(self, **kwargs):
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.records.append(kwargs)


class RuntimePermissionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.authorizer = FakeAuthorizer()
        self.audit = FakeAudit()
        self.interrupts = []
        self.executed = []

        async def interrupt(tenant_id, run_id):
            self.interrupts.append((tenant_id, run_id))

        self.guard = LeastPrivilegeRuntimeGuard(
            authorizer=self.authorizer, audit=self.audit, interrupt_run=interrupt,
        )
        self.call = RuntimeCall("tenant-a", "run-a", "agent-a", "file", "f-1", "file.read")

        async def handler(arguments):
            self.executed.append(dict(arguments))
            return "ok"

        self.handler = handler

    async def test_authorizes_each_invocation_without_allow_cache(self):
        self.authorizer.decisions = [
            PermissionDecision(True, "v1"),
            PermissionDecision(False, "v2", "revoked"),
        ]
        self.assertEqual(await self.guard.invoke(
            call=self.call, handler=self.handler, arguments={"n": 1},
        ), "ok")
        with self.assertRaises(RuntimePermissionDenied):
            await self.guard.invoke(call=self.call, handler=self.handler, arguments={"n": 2})
        self.assertEqual(len(self.authorizer.calls), 2)
        self.assertEqual(self.executed, [{"n": 1}])
        self.assertEqual(self.interrupts, [("tenant-a", "run-a")])

    async def test_denial_is_audited_and_interrupts_run_before_handler(self):
        self.authorizer.decisions = [PermissionDecision(False, "v3", "scope_mismatch")]
        with self.assertRaises(RuntimePermissionDenied):
            await self.guard.invoke(call=self.call, handler=self.handler, arguments={})
        self.assertEqual(self.audit.records[0]["outcome"], "denied")
        self.assertEqual(self.audit.records[0]["reason"], "scope_mismatch")
        self.assertEqual(self.executed, [])
        self.assertEqual(len(self.interrupts), 1)

    async def test_authorizer_failure_fails_closed_and_interrupts(self):
        self.authorizer.error = RuntimeError("policy store down")
        with self.assertRaises(RuntimePermissionDenied):
            await self.guard.invoke(call=self.call, handler=self.handler, arguments={})
        self.assertEqual(self.executed, [])
        self.assertEqual(len(self.interrupts), 1)
        self.assertEqual(self.audit.records[0]["outcome"], "denied")

    async def test_authorized_audit_failure_blocks_handler_and_interrupts(self):
        self.audit.fail = True
        with self.assertRaises(RuntimePermissionDenied):
            await self.guard.invoke(call=self.call, handler=self.handler, arguments={})
        self.assertEqual(self.executed, [])
        self.assertEqual(len(self.interrupts), 1)

    async def test_handler_failure_is_result_audited(self):
        async def fail(_arguments):
            raise ValueError("provider failed")
        with self.assertRaises(ValueError):
            await self.guard.invoke(call=self.call, handler=fail, arguments={})
        self.assertEqual(self.audit.records[0]["outcome"], "authorized")
        self.assertEqual(self.audit.records[1]["outcome"], "failed")

    async def test_call_context_requires_exact_nonempty_scope(self):
        with self.assertRaises(ValueError):
            RuntimeCall("tenant-a", "run-a", "actor", "file", "", "file.read")


if __name__ == "__main__":
    unittest.main()
