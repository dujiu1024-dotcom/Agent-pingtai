import unittest
from types import SimpleNamespace

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.strands_mcp_provider import (
    GatewayMCPEndpoint,
    StrandsMCPGatewayToolProvider,
)


class FakeMCPClient:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.entered = False
        self.closed = False
        self.tools = [SimpleNamespace(tool_name="read_document")]

    def __enter__(self):
        self.entered = True
        return self

    def list_tools_sync(self):
        if not self.entered:
            raise AssertionError("client must be entered before tool discovery")
        return self.tools

    def __exit__(self, exc_type, exc, traceback):
        self.closed = True


class StrandsMCPGatewayToolProviderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = RunContext(
            tenant_id="tenant-a",
            run_id="run-001",
            agent_id="expert-a",
            model_template=ModelTemplateRef("general", 1),
        )
        self.clients = []
        self.resolver_calls = []

    def client_factory(self, **kwargs):
        client = FakeMCPClient(**kwargs)
        self.clients.append(client)
        return client

    def resolver(self, context, allowed_tools):
        self.resolver_calls.append((context, tuple(allowed_tools)))
        return GatewayMCPEndpoint(
            url="https://gateway.internal/runs/run-001/mcp",
            bearer_token="short-lived-run-token",
        )

    def provider(self):
        return StrandsMCPGatewayToolProvider(
            endpoint_resolver=self.resolver,
            client_factory=self.client_factory,
        )

    async def test_connects_with_run_scoped_auth_and_exact_tool_filter(self):
        async with self.provider().open_session(
            context=self.context, allowed_tools=("read_document",)
        ) as tools:
            self.assertEqual([tool.tool_name for tool in tools], ["read_document"])
            client = self.clients[0]
            self.assertTrue(client.entered)
            self.assertFalse(client.closed)
            self.assertEqual(
                client.options["url"],
                "https://gateway.internal/runs/run-001/mcp",
            )
            self.assertEqual(
                client.options["headers"],
                {"Authorization": "Bearer short-lived-run-token"},
            )
            self.assertEqual(
                client.options["tool_filters"],
                {"allowed": ["read_document"]},
            )
            self.assertFalse(client.options["continue_on_error"])
            self.assertEqual(
                self.resolver_calls,
                [(self.context, ("read_document",))],
            )
        self.assertTrue(self.clients[0].closed)

    async def test_rejects_discovery_mismatch_and_closes_client(self):
        provider = self.provider()
        with self.assertRaisesRegex(RuntimeError, "does not exactly match"):
            async with provider.open_session(
                context=self.context,
                allowed_tools=("read_document", "send_message"),
            ):
                self.fail("Mismatched tool set must not be yielded")
        self.assertTrue(self.clients[0].closed)

    async def test_closes_client_when_agent_body_fails(self):
        with self.assertRaisesRegex(RuntimeError, "agent failed"):
            async with self.provider().open_session(
                context=self.context, allowed_tools=("read_document",)
            ):
                raise RuntimeError("agent failed")
        self.assertTrue(self.clients[0].closed)

    async def test_rejects_empty_or_duplicate_allowlist(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            async with self.provider().open_session(
                context=self.context, allowed_tools=()
            ):
                pass
        with self.assertRaisesRegex(ValueError, "unique"):
            async with self.provider().open_session(
                context=self.context,
                allowed_tools=("read_document", "read_document"),
            ):
                pass
        self.assertEqual(self.clients, [])

    def test_endpoint_requires_https_and_short_lived_token(self):
        with self.assertRaisesRegex(ValueError, "HTTPS"):
            GatewayMCPEndpoint(
                url="http://gateway.internal/mcp",
                bearer_token="token",
            )
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            GatewayMCPEndpoint(
                url="https://gateway.internal/mcp",
                bearer_token=" ",
            )


if __name__ == "__main__":
    unittest.main()
