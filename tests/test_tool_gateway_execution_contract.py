import asyncio
import unittest

from agent_platform.approval.policy import RiskLevel
from agent_platform.tools.gateway import SupervisedToolGateway, ToolDefinition
from agent_platform.tools.execution_contract import (
    GatewayPrincipal, ToolCallCancelled, ToolCallDenied, ToolCallTimedOut,
    ToolGatewayContract,
)


class Audit:
    def __init__(self):
        self.records = []
    async def record(self, **kwargs):
        self.records.append(kwargs)


class ContractTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.started = asyncio.Event()
        self.executed = []
        self.principal = GatewayPrincipal(
            tenant_id="tenant-a", actor_id="actor-a", actor_roles=("operator",),
            run_id="run-a", agent_id="agent-a", allowed_tool_names=("slow", "read"),
        )
        async def slow(_args):
            self.started.set()
            await asyncio.sleep(10)
        async def read(args):
            self.executed.append(dict(args))
            return {"ok": True}
        async def authorize(_ctx, _args):
            return True
        self.gateway = SupervisedToolGateway(
            tools=(
                ToolDefinition("slow", RiskLevel.READ_ONLY, slow,
                    resource_resolver=lambda _a: "run:run-a",
                    resource_authorizer=authorize),
                ToolDefinition("read", RiskLevel.READ_ONLY, read,
                    resource_resolver=lambda _a: "run:run-a",
                    resource_authorizer=authorize),
            ),
            approval_verifier=type("Approvals", (), {"consume": self.consume})(),
            audit_sink=type("GatewayAudit", (), {"record": self.gateway_audit})(),
        )
        self.audit = Audit()
        self.resolve = lambda token: self.resolve_token(token)
        self.contract = ToolGatewayContract(
            gateway=self.gateway, resolve_principal=self.resolve,
            authorize_run=self.authorize, audit=self.audit,
        )
        self.valid = True
        self.run_allowed = True
    async def consume(self, **_kwargs): return False
    async def gateway_audit(self, **_kwargs): pass
    async def resolve_token(self, token):
        return self.principal if token == "valid-token" and self.valid else None
    async def authorize(self, _principal): return self.run_allowed

    async def test_authentication_and_run_binding_fail_closed(self):
        with self.assertRaises(ToolCallDenied):
            await self.contract.call(bearer_token="bad", tool_name="read", arguments={}, call_id="c1")
        self.run_allowed = False
        with self.assertRaises(ToolCallDenied):
            await self.contract.call(bearer_token="valid-token", tool_name="read", arguments={}, call_id="c2")
        self.assertEqual(self.executed, [])
        self.assertEqual(self.audit.records[-1]["outcome"], "denied")

    async def test_run_allowlist_and_trusted_identity(self):
        with self.assertRaises(ToolCallDenied):
            await self.contract.call(bearer_token="valid-token", tool_name="admin", arguments={}, call_id="c1")
        result = await self.contract.call(
            bearer_token="valid-token", tool_name="read",
            arguments={"value": 1, "actor_id": "spoofed"}, call_id="c2",
        )
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(self.executed), 1)

    async def test_timeout_cancels_handler_and_is_audited(self):
        with self.assertRaises(ToolCallTimedOut):
            await self.contract.call(
                bearer_token="valid-token", tool_name="slow", arguments={},
                call_id="timeout", timeout_seconds=0.01,
            )
        self.assertTrue(self.started.is_set())
        self.assertEqual(self.audit.records[-1]["action"], "tool.call.timed_out")
        self.assertEqual(self.audit.records[-1]["detail"]["status"], "timed_out")

    async def test_run_cancellation_cancels_handler_and_is_audited(self):
        cancellation = asyncio.Event()
        task = asyncio.create_task(self.contract.call(
            bearer_token="valid-token", tool_name="slow", arguments={},
            call_id="cancel", timeout_seconds=5, cancellation=cancellation,
        ))
        await self.started.wait()
        cancellation.set()
        with self.assertRaises(ToolCallCancelled):
            await task
        self.assertEqual(self.audit.records[-1]["action"], "tool.call.cancelled")
        self.assertEqual(self.audit.records[-1]["detail"]["status"], "cancelled")

    async def test_caller_task_cancellation_propagates(self):
        task = asyncio.create_task(self.contract.call(
            bearer_token="valid-token", tool_name="slow", arguments={},
            call_id="caller-cancel", timeout_seconds=5,
        ))
        await self.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.audit.records[-1]["outcome"], "cancelled")


if __name__ == "__main__":
    unittest.main()
