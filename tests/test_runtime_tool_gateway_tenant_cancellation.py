import asyncio
import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.tool_gateway import (
    InMemoryToolAuditSink,
    InMemoryToolRunRegistry,
    ToolDefinition,
    ToolGateway,
    ToolPrincipal,
    ToolRunBinding,
)


class Authenticator:
    def __init__(self, principals):
        self.principals = principals

    async def authenticate(self, credential):
        return self.principals.get(credential)


class TenantScopedCancellationTests(unittest.IsolatedAsyncioTestCase):
    async def test_cancelling_run_in_one_tenant_does_not_cancel_same_run_id_in_another(self):
        run_id = "shared-run-id"
        started = {"tenant-a": asyncio.Event(), "tenant-b": asyncio.Event()}
        cancelled = {"tenant-a": asyncio.Event(), "tenant-b": asyncio.Event()}

        async def handler(_arguments, context):
            started[context.tenant_id].set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled[context.tenant_id].set()

        principals = {
            "credential-a": ToolPrincipal("actor-a", "tenant-a", "agent-a"),
            "credential-b": ToolPrincipal("actor-b", "tenant-b", "agent-b"),
        }
        registry = InMemoryToolRunRegistry()
        registry.bind(ToolRunBinding(
            tenant_id="tenant-a", run_id=run_id, agent_id="agent-a",
            allowed_tools=frozenset({"wait"}),
        ))
        registry.bind(ToolRunBinding(
            tenant_id="tenant-b", run_id=run_id, agent_id="agent-b",
            allowed_tools=frozenset({"wait"}),
        ))
        gateway = ToolGateway(
            authenticator=Authenticator(principals),
            run_registry=registry,
            audit_sink=InMemoryToolAuditSink(),
        )
        gateway.register(ToolDefinition("wait", handler, timeout_seconds=30))

        def context(tenant_id, agent_id):
            return RunContext(
                tenant_id=tenant_id,
                run_id=run_id,
                agent_id=agent_id,
                model_template=ModelTemplateRef("benchmark-model", 1),
            )

        task_a = asyncio.create_task(gateway.invoke(
            credential="credential-a", run_context=context("tenant-a", "agent-a"),
            tool_name="wait", arguments={}, call_id="call-a",
        ))
        task_b = asyncio.create_task(gateway.invoke(
            credential="credential-b", run_context=context("tenant-b", "agent-b"),
            tool_name="wait", arguments={}, call_id="call-b",
        ))
        await asyncio.gather(started["tenant-a"].wait(), started["tenant-b"].wait())

        self.assertEqual(await gateway.cancel_run(tenant_id="tenant-a", run_id=run_id), 1)
        with self.assertRaises(asyncio.CancelledError):
            await task_a
        self.assertTrue(cancelled["tenant-a"].is_set())
        self.assertFalse(task_b.done())
        self.assertFalse(cancelled["tenant-b"].is_set())

        self.assertEqual(await gateway.cancel_run(tenant_id="tenant-b", run_id=run_id), 1)
        with self.assertRaises(asyncio.CancelledError):
            await task_b
        self.assertTrue(cancelled["tenant-b"].is_set())


if __name__ == "__main__":
    unittest.main()
