import unittest

from agent_platform.approval.policy import ActionRequest, RiskLevel
from agent_platform.tools.gateway import (
    SupervisedToolGateway,
    ToolDefinition,
    ToolExecutionDenied,
)
from agent_platform.tools.gateway_mcp import (
    GatewayMCPBridge,
    GatewaySessionContext,
)


class FakeApprovalVerifier:
    def __init__(self, valid=False):
        self.valid = valid
        self.calls = []

    async def consume(self, *, tenant_id, approval_id, action_digest):
        self.calls.append((tenant_id, approval_id, action_digest))
        return self.valid


class FakeAuditSink:
    def __init__(self):
        self.records = []

    async def record(self, **kwargs):
        self.records.append(kwargs)


class GatewayMCPBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema is installed in the dedicated platform CI job")
        self.executed = []
        self.execution_contexts = []
        self.approvals = FakeApprovalVerifier()
        self.audit = FakeAuditSink()

        async def read_document(arguments):
            self.executed.append(dict(arguments))
            return {"document": "approved"}

        async def send_message(arguments):
            self.executed.append(dict(arguments))
            return {"sent": True}

        async def authorize_document(context, arguments):
            return (
                context.tenant_id == "tenant-a"
                and arguments["document_id"] != "doc-secret"
            )

        async def authorize_message(context, arguments):
            return context.tenant_id == "tenant-a"

        async def contextual_handler(arguments, context):
            self.execution_contexts.append(context)
            return await read_document(arguments)

        async def contextual_message_handler(arguments, context):
            self.execution_contexts.append(context)
            return await send_message(arguments)

        self.tools = (
            ToolDefinition(
                name="read_document",
                risk=RiskLevel.READ_ONLY,
                handler=read_document,
                description="Read one authorized document.",
                allowed_roles=("analyst",),
                input_schema={
                    "type": "object",
                    "properties": {"document_id": {"type": "string"}},
                    "required": ["document_id"],
                    "additionalProperties": False,
                },
                resource_resolver=lambda args: f"document:{args['document_id']}",
                resource_authorizer=authorize_document,
                contextual_handler=contextual_handler,
            ),
            ToolDefinition(
                name="send_message",
                risk=RiskLevel.EXTERNAL_OR_IRREVERSIBLE,
                handler=send_message,
                description="Send an approved message.",
                allowed_roles=("operator",),
                input_schema={
                    "type": "object",
                    "properties": {
                        "recipient": {"type": "string"},
                        "text": {"type": "string"},
                    },
                    "required": ["recipient", "text"],
                    "additionalProperties": False,
                },
                resource_resolver=lambda args: f"recipient:{args['recipient']}",
                resource_authorizer=authorize_message,
                contextual_handler=contextual_message_handler,
            ),
            ToolDefinition(
                name="unconfigured",
                risk=RiskLevel.READ_ONLY,
                handler=read_document,
            ),
        )
        self.gateway = SupervisedToolGateway(
            tools=self.tools,
            approval_verifier=self.approvals,
            audit_sink=self.audit,
        )

    def bridge(self, **overrides):
        values = {
            "tenant_id": "tenant-a",
            "actor_id": "agent-researcher",
            "actor_roles": ("analyst",),
            "run_id": "run-001",
            "agent_id": "researcher",
            "allowed_tool_names": ("read_document",),
            "approval_ids": {},
        }
        values.update(overrides)
        return GatewayMCPBridge(
            gateway=self.gateway,
            session=GatewaySessionContext(**values),
        )

    async def test_only_run_allowlisted_tools_are_discovered(self):
        bridge = self.bridge(
            allowed_tool_names=("read_document", "send_message")
        )
        self.assertEqual(
            [item["name"] for item in bridge.list_tools()],
            ["read_document"],
        )

    async def test_tenant_and_actor_are_taken_from_trusted_session(self):
        bridge = self.bridge()
        await bridge.call_tool(
            name="read_document", arguments={"document_id": "doc-17"}
        )
        self.assertEqual(self.executed, [{"document_id": "doc-17"}])
        self.assertEqual(self.audit.records[0]["tenant_id"], "tenant-a")
        self.assertEqual(self.audit.records[0]["actor_id"], "agent-researcher")
        self.assertEqual(self.audit.records[0]["resource"], "document:doc-17")
        self.assertEqual(self.audit.records[0]["run_id"], "run-001")
        self.assertEqual(self.audit.records[0]["agent_id"], "researcher")
        self.assertEqual(self.execution_contexts[0].tenant_id, "tenant-a")
        self.assertEqual(self.execution_contexts[0].run_id, "run-001")
        self.assertEqual(self.execution_contexts[0].agent_id, "researcher")

    async def test_resource_authorizer_blocks_cross_tenant_or_forbidden_resource(self):
        bridge = self.bridge()
        with self.assertRaisesRegex(ToolExecutionDenied, "resource access"):
            await bridge.call_tool(
                name="read_document", arguments={"document_id": "doc-secret"}
            )
        self.assertEqual(self.executed, [])
        self.assertEqual(self.audit.records[-1]["outcome"], "denied")

    async def test_model_cannot_inject_identity_or_approval_fields(self):
        bridge = self.bridge()
        with self.assertRaises(ToolExecutionDenied):
            await bridge.call_tool(
                name="read_document",
                arguments={
                    "document_id": "doc-17",
                    "tenant_id": "tenant-victim",
                    "actor_id": "admin",
                    "approval_id": "forged",
                },
            )
        self.assertEqual(self.executed, [])

    async def test_tool_not_allowlisted_for_run_is_denied(self):
        bridge = self.bridge()
        with self.assertRaises(ToolExecutionDenied):
            await bridge.call_tool(
                name="send_message",
                arguments={"recipient": "x", "text": "hello"},
            )
        self.assertEqual(self.executed, [])

    async def test_l2_approval_id_is_injected_by_trusted_session(self):
        action_digest = ActionRequest(
            action="send_message",
            resource="recipient:customer-1",
            parameters={"recipient": "customer-1", "text": "hello"},
            actor_id="agent-researcher",
        ).digest()
        bridge = self.bridge(
            actor_roles=("operator",),
            allowed_tool_names=("send_message",),
            approval_ids={action_digest: "approval-123"},
        )
        with self.assertRaises(ToolExecutionDenied):
            await bridge.call_tool(
                name="send_message",
                arguments={"recipient": "customer-1", "text": "hello"},
            )
        self.approvals.valid = True
        result = await bridge.call_tool(
            name="send_message",
            arguments={"recipient": "customer-1", "text": "hello"},
        )
        self.assertEqual(result, {"sent": True})
        self.assertEqual(self.approvals.calls[0][0:2], ("tenant-a", "approval-123"))

    async def test_unconfigured_tool_cannot_be_exposed(self):
        with self.assertRaisesRegex(ValueError, "JSON Schema"):
            self.bridge(allowed_tool_names=("unconfigured",))

    async def test_invalid_schema_and_resource_are_rejected(self):
        bridge = self.bridge()
        with self.assertRaises(ToolExecutionDenied):
            await bridge.call_tool(name="read_document", arguments={})
        self.assertEqual(self.executed, [])

    def test_session_rejects_malformed_approval_digest(self):
        with self.assertRaisesRegex(ValueError, "SHA-256 action digest"):
            self.bridge(approval_ids={"send_message": "approval-1"})

    async def test_server_factory_uses_optional_mcp_sdk(self):
        try:
            from mcp.server import Server
        except ImportError:
            self.skipTest("MCP SDK is installed in the dedicated SDK CI job")
        from agent_platform.tools.gateway_mcp import create_mcp_server

        server = create_mcp_server(bridge=self.bridge())
        self.assertIsInstance(server, Server)


if __name__ == "__main__":
    unittest.main()
