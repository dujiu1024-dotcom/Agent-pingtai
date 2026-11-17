import unittest

from agent_platform.runtime.langgraph_adapter import (
    GatewayToolClient,
    GatewayToolNode,
    build_state_graph,
)


class FakeGateway:
    def __init__(self):
        self.calls = []

    async def call_resolved(self, **kwargs):
        self.calls.append(kwargs)
        return {"ok": True}


class LangGraphGatewayContractTests(unittest.IsolatedAsyncioTestCase):
    def test_graph_requires_explicit_tool_node_inventory(self):
        with self.assertRaises(TypeError):
            build_state_graph(state_schema=dict, nodes={})

    def test_graph_rejects_plain_callable_for_declared_tool_node(self):
        async def node(state, config):
            return state

        with self.assertRaisesRegex(ValueError, "must use GatewayToolNode"):
            build_state_graph(
                state_schema=dict, nodes={"search": node},
                tool_using_nodes=("search",),
            )

    def test_graph_rejects_missing_declared_tool_node(self):
        with self.assertRaisesRegex(ValueError, "not registered"):
            build_state_graph(
                state_schema=dict, nodes={}, tool_using_nodes=("search",),
            )

    async def test_gateway_node_uses_run_scoped_trusted_identity(self):
        gateway = FakeGateway()

        async def handler(state, tools):
            result = await tools.call("search", {"query": state["query"]})
            return {"result": result}

        node = GatewayToolNode(
            handler=handler, gateway=gateway, allowed_tools=("search",),
            identity_resolver=lambda _: {
                "tenant_id": "tenant-a", "actor_id": "agent-a",
                "actor_roles": ("agent",), "agent_id": "researcher",
            },
        )
        result = await node(
            {"query": "safe query"},
            {"configurable": {
                "platform_tenant_id": "tenant-a", "platform_run_id": "run-a",
                "platform_correlation_id": "corr-a",
            }},
        )
        self.assertEqual(result, {"result": {"ok": True}})
        call = gateway.calls[0]
        self.assertEqual(call["tenant_id"], "tenant-a")
        self.assertEqual(call["run_id"], "run-a")
        self.assertEqual(call["actor_id"], "agent-a")
        self.assertEqual(call["tool_name"], "search")
        self.assertTrue(call["call_id"].startswith("lg-"))

    async def test_gateway_node_rejects_tenant_spoof_and_unlisted_tools(self):
        gateway = FakeGateway()
        node = GatewayToolNode(
            handler=lambda state, tools: tools.call("delete", {}),
            gateway=gateway, allowed_tools=("search",),
            identity_resolver=lambda _: {
                "tenant_id": "other-tenant", "actor_id": "agent-a",
                "actor_roles": ("agent",),
            },
        )
        with self.assertRaisesRegex(PermissionError, "tenant"):
            await node({}, {"configurable": {
                "platform_tenant_id": "tenant-a", "platform_run_id": "run-a",
            }})
        self.assertEqual(gateway.calls, [])

    async def test_gateway_client_rejects_unlisted_tool(self):
        gateway = FakeGateway()
        client = GatewayToolClient(
            gateway=gateway, allowed_tools=("search",),
            identity={"tenant_id": "tenant-a", "actor_id": "agent-a", "actor_roles": ("agent",)},
            run_id="run-a",
        )
        with self.assertRaisesRegex(PermissionError, "not allowed"):
            await client.call("delete", {})
        self.assertEqual(gateway.calls, [])


    async def test_gateway_node_requires_trusted_tenant_and_run_before_handler(self):
        gateway = FakeGateway()
        invoked = []
        node = GatewayToolNode(
            handler=lambda state, tools: invoked.append(True),
            gateway=gateway, allowed_tools=("search",),
            identity_resolver=lambda _: {
                "tenant_id": "tenant-a", "actor_id": "agent-a", "actor_roles": ("agent",),
            },
        )
        invalid_configs = (
            {},
            {"configurable": {"platform_run_id": "run-a"}},
            {"configurable": {"platform_tenant_id": "tenant-a"}},
            {"configurable": None},
        )
        for config in invalid_configs:
            with self.subTest(config=config):
                with self.assertRaises(PermissionError):
                    await node({}, config)
        self.assertEqual(invoked, [])
        self.assertEqual(gateway.calls, [])

    async def test_gateway_node_rejects_malformed_trusted_identity_before_handler(self):
        gateway = FakeGateway()
        invoked = []
        identities = (
            {"tenant_id": "tenant-a", "actor_id": "agent-a"},
            {"tenant_id": "tenant-a", "actor_id": "agent-a", "actor_roles": "admin"},
        )
        for identity in identities:
            node = GatewayToolNode(
                handler=lambda state, tools: invoked.append(True),
                gateway=gateway, allowed_tools=("search",),
                identity_resolver=lambda _config, identity=identity: identity,
            )
            with self.subTest(identity=identity):
                with self.assertRaises(PermissionError):
                    await node({}, {"configurable": {
                        "platform_tenant_id": "tenant-a", "platform_run_id": "run-a",
                    }})
        self.assertEqual(invoked, [])
        self.assertEqual(gateway.calls, [])

    async def test_gateway_client_passes_correlation_and_trusted_identity_separately(self):
        gateway = FakeGateway()
        client = GatewayToolClient(
            gateway=gateway, allowed_tools=("search",),
            identity={"tenant_id": "tenant-a", "actor_id": "agent-a",
                      "actor_roles": ("agent",), "agent_id": "researcher"},
            run_id="run-a", correlation_id="corr-a",
        )
        await client.call("search", {"query": "x", "tenant_id": "spoofed"})
        call = gateway.calls[0]
        self.assertEqual(call["tenant_id"], "tenant-a")
        self.assertEqual(call["actor_id"], "agent-a")
        self.assertEqual(call["run_id"], "run-a")
        self.assertEqual(call["correlation_id"], "corr-a")
        self.assertEqual(call["arguments"]["tenant_id"], "spoofed")

    async def test_gateway_dependency_failure_propagates_without_fallback_execution(self):
        class UnavailableGateway:
            async def call_resolved(self, **_kwargs):
                raise ConnectionError("tool gateway unavailable")

        invoked = []
        client = GatewayToolClient(
            gateway=UnavailableGateway(), allowed_tools=("search",),
            identity={"tenant_id": "tenant-a", "actor_id": "agent-a",
                      "actor_roles": ("agent",)},
            run_id="run-a",
        )
        with self.assertRaisesRegex(ConnectionError, "unavailable"):
            await client.call("search", {"query": "x"})
        self.assertEqual(invoked, [])


if __name__ == "__main__":
    unittest.main()
