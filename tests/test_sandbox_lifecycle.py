import unittest

from agent_platform.runtime.lifecycle import ToolCallRegistry
from agent_platform.sandbox.lifecycle import make_sandbox_tool_handler
from agent_platform.sandbox.oci import SandboxResult
from agent_platform.tools.gateway import ToolExecutionContext


class FakeRunner:
    def __init__(self):
        self.calls = []

    async def run(self, command, *, stdin=b"", environment=None, timeout_seconds=30.0):
        self.calls.append((command, stdin, environment, timeout_seconds))
        return SandboxResult(0, b"ok", b"", False, False)


class SandboxLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_handler_requires_run_and_uses_run_scoped_call_registry(self):
        runner = FakeRunner()
        handler = make_sandbox_tool_handler(runner, ToolCallRegistry())
        context = ToolExecutionContext(
            tenant_id="tenant-a", actor_id="user-a", actor_roles=("operator",),
            run_id="run-a", agent_id="agent-a", tool_name="sandbox.execute",
            resource="sandbox:run-a",
        )
        result = await handler({
            "call_id": "call-a", "command": ["python", "-I", "-c", "print(1)"],
            "stdin": "input", "timeout_seconds": 12,
        }, context)
        self.assertEqual(result["stdout"], "ok")
        self.assertEqual(runner.calls[0][1], b"input")
        self.assertEqual(runner.calls[0][3], 12.0)

    async def test_handler_rejects_missing_run_context(self):
        runner = FakeRunner()
        handler = make_sandbox_tool_handler(runner, ToolCallRegistry())
        context = ToolExecutionContext(
            tenant_id="tenant-a", actor_id="user-a", actor_roles=(), run_id=None,
            agent_id=None, tool_name="sandbox.execute", resource="sandbox",
        )
        with self.assertRaisesRegex(ValueError, "supervised Run"):
            await handler({"call_id": "call-a", "command": ["python"]}, context)

    async def test_handler_rejects_invalid_command_shape(self):
        handler = make_sandbox_tool_handler(FakeRunner(), ToolCallRegistry())
        context = ToolExecutionContext(
            tenant_id="tenant-a", actor_id="user-a", actor_roles=(), run_id="run-a",
            agent_id=None, tool_name="sandbox.execute", resource="sandbox:run-a",
        )
        with self.assertRaisesRegex(ValueError, "argv list"):
            await handler({"call_id": "call-a", "command": "python"}, context)


if __name__ == "__main__":
    unittest.main()
