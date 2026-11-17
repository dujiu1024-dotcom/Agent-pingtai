import socket
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone

import uvicorn

from agent_platform.approval.policy import RiskLevel
from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.strands_mcp_provider import (
    GatewayMCPEndpoint,
    StrandsMCPGatewayToolProvider,
)
from agent_platform.tools.gateway import (
    SupervisedToolGateway,
    ToolDefinition,
)
from agent_platform.tools.gateway_mcp import (
    GatewayMCPBridge,
    GatewaySessionContext,
)
from agent_platform.tools.gateway_mcp_http import create_authenticated_mcp_asgi_app


class FakeApprovalVerifier:
    async def consume(self, *, tenant_id, approval_id, action_digest, policy_version="1"):
        return False


class FakeAuditSink:
    def __init__(self):
        self.records = []

    async def record(self, **kwargs):
        self.records.append(kwargs)


class StrandsGatewayMCPHttpIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_official_strands_mcp_client_discovers_and_invokes_gateway_tool(self):
        executed = []
        audit = FakeAuditSink()

        async def handler(arguments):
            executed.append(dict(arguments))
            return {"document": "verified", "id": arguments["document_id"]}

        async def authorize(context, arguments):
            return context.tenant_id == "tenant-a" and arguments["document_id"] != "secret"

        async def contextual_handler(arguments, context):
            self.assertEqual(context.run_id, "run-001")
            self.assertEqual(context.agent_id, "researcher")
            return await handler(arguments)

        definition = ToolDefinition(
            name="read_document",
            risk=RiskLevel.READ_ONLY,
            handler=handler,
            description="Read an authorized document.",
            allowed_roles=("analyst",),
            input_schema={
                "type": "object",
                "properties": {"document_id": {"type": "string"}},
                "required": ["document_id"],
                "additionalProperties": False,
            },
            resource_resolver=lambda args: f"document:{args['document_id']}",
            resource_authorizer=authorize,
            contextual_handler=contextual_handler,
        )
        gateway = SupervisedToolGateway(
            tools=(definition,),
            approval_verifier=FakeApprovalVerifier(),
            audit_sink=audit,
        )
        session = GatewaySessionContext(
            tenant_id="tenant-a",
            actor_id="agent-researcher",
            actor_roles=("analyst",),
            run_id="run-001",
            agent_id="researcher",
            allowed_tool_names=("read_document",),
            approval_ids={},
        )
        async def authorize_live_run(current):
            return (
                current.tenant_id == "tenant-a"
                and current.run_id == "run-001"
                and current.agent_id == "researcher"
            )

        bridge = GatewayMCPBridge(
            gateway=gateway, session=session, authorize_run=authorize_live_run
        )
        token = "ephemeral-run-token-for-integration-test"

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        endpoint_url = f"http://127.0.0.1:{port}/runs/run-001/mcp"
        app = create_authenticated_mcp_asgi_app(
            bridge=bridge,
            bearer_token=token,
            issuer_url="https://identity.internal",
            resource_server_url=endpoint_url,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=2),
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="critical",
                lifespan="on",
            )
        )
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(server.started, "Gateway MCP HTTP server did not start")

            run_context = RunContext(
                tenant_id="tenant-a",
                run_id="run-001",
                agent_id="researcher",
                model_template=ModelTemplateRef("general", 1),
            )
            provider = StrandsMCPGatewayToolProvider(
                endpoint_resolver=lambda context, allowed: GatewayMCPEndpoint(
                    url=endpoint_url,
                    bearer_token=token,
                )
            )
            async with provider.open_session(
                context=run_context,
                allowed_tools=("read_document",),
            ) as tools:
                self.assertEqual([tool.tool_name for tool in tools], ["read_document"])
                events = [
                    event
                    async for event in tools[0].stream(
                        {
                            "toolUseId": "tool-call-001",
                            "name": "read_document",
                            "input": {"document_id": "doc-17"},
                        },
                        {},
                    )
                ]
                self.assertEqual(len(events), 1)

            self.assertEqual(executed, [{"document_id": "doc-17"}])
            self.assertEqual(audit.records[-1]["outcome"], "succeeded")
            self.assertEqual(audit.records[-1]["run_id"], "run-001")
            self.assertEqual(audit.records[-1]["agent_id"], "researcher")
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            self.assertFalse(thread.is_alive(), "Gateway MCP HTTP server did not stop")


if __name__ == "__main__":
    unittest.main()
