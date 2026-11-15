import unittest

from agent_platform.tools.mcp_adapter import MCPToolCallError, make_mcp_handler


class FakeMCPResult:
    def __init__(self, is_error=False):
        self.isError = is_error
        self.content = [{"type": "text", "text": "result"}]


class FakeMCPSession:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def call_tool(self, name, *, arguments):
        self.calls.append((name, arguments))
        return self.result


class MCPAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_uses_bound_tool_name_and_arguments(self):
        session = FakeMCPSession(FakeMCPResult())
        handler = make_mcp_handler(session, remote_tool_name="search_docs")
        result = await handler({"query": "policy"})
        self.assertIs(result, session.result)
        self.assertEqual(
            session.calls,
            [("search_docs", {"query": "policy"})],
        )

    async def test_remote_tool_error_is_rejected(self):
        session = FakeMCPSession(FakeMCPResult(is_error=True))
        handler = make_mcp_handler(session, remote_tool_name="send_message")
        with self.assertRaises(MCPToolCallError):
            await handler({"text": "hello"})

    async def test_empty_remote_tool_name_is_rejected(self):
        with self.assertRaises(ValueError):
            make_mcp_handler(FakeMCPSession(FakeMCPResult()), remote_tool_name=" ")


if __name__ == "__main__":
    unittest.main()
