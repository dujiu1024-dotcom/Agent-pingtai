import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from agent_platform.control_plane.gateway_auth import PersistentRunTokenVerifier
from agent_platform.tools.gateway_mcp_http import (
    RunBoundTokenVerifier,
    create_authenticated_mcp_asgi_app,
)


class EmptyGatewayBridge:
    def __init__(self):
        self.session = SimpleNamespace(actor_id="agent-researcher")

    def list_tools(self):
        return []

    async def call_tool(self, *, name, arguments):
        raise AssertionError("No tools are registered in this HTTP smoke fixture")


class GatewayMCPHttpTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_token_verifier_checks_token_expiry_and_revocation(self):
        verifier = RunBoundTokenVerifier(
            bearer_token="run-secret",
            actor_id="agent-a",
            resource_server_url="https://gateway.example.com/runs/run-a/mcp",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        access = await verifier.verify_token("run-secret")
        self.assertIsNotNone(access)
        self.assertEqual(access.client_id, "agent-a")
        self.assertEqual(access.scopes, ["platform:tools"])
        self.assertIsNone(await verifier.verify_token("other-token"))
        verifier.revoke()
        self.assertIsNone(await verifier.verify_token("run-secret"))

    async def test_expired_run_token_is_rejected(self):
        verifier = RunBoundTokenVerifier(
            bearer_token="run-secret",
            actor_id="agent-a",
            resource_server_url="https://gateway.example.com/runs/run-a/mcp",
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
        )
        self.assertIsNone(await verifier.verify_token("run-secret"))

    def test_http_app_requires_explicit_host_policy_for_non_loopback(self):
        with self.assertRaisesRegex(ValueError, "explicit transport_security"):
            create_authenticated_mcp_asgi_app(
                bridge=EmptyGatewayBridge(),
                bearer_token="run-secret",
                issuer_url="https://identity.example.com",
                resource_server_url="https://gateway.example.com/runs/run-a/mcp",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
                host="0.0.0.0",
            )

    def test_creates_streamable_http_app_with_bearer_auth(self):
        try:
            from starlette.testclient import TestClient
        except ImportError:
            self.skipTest("Starlette test client is provided by the MCP SDK dependency")

        app = create_authenticated_mcp_asgi_app(
            bridge=EmptyGatewayBridge(),
            bearer_token="run-secret",
            issuer_url="https://identity.example.com",
            resource_server_url="https://gateway.example.com/runs/run-a/mcp",
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=1),
        )
        initialize = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "strands-provider-smoke", "version": "1"},
            },
        }
        with TestClient(app) as client:
            denied = client.post(
                "/runs/run-a/mcp",
                json=initialize,
                headers={"Accept": "application/json, text/event-stream"},
            )
            self.assertEqual(denied.status_code, 401)

            accepted = client.post(
                "/runs/run-a/mcp",
                json=initialize,
                headers={
                    "Authorization": "Bearer run-secret",
                    "Accept": "application/json, text/event-stream",
                },
            )
            self.assertEqual(accepted.status_code, 200)
            self.assertTrue(accepted.headers.get("mcp-session-id"))
            self.assertIn("result", accepted.json())

            app.state.gateway_token_verifier.revoke()
            revoked = client.post(
                "/runs/run-a/mcp",
                json=initialize,
                headers={
                    "Authorization": "Bearer run-secret",
                    "Accept": "application/json, text/event-stream",
                },
            )
            self.assertEqual(revoked.status_code, 401)


if __name__ == "__main__":
    unittest.main()


class PersistentRunTokenVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_persistent_token_must_match_every_mcp_session_identity_claim(self):
        expected = {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "actor_id": "actor-a",
        }

        class TokenService:
            def __init__(self, record):
                self.record = record
                self.calls = []

            async def resolve(self, token, *, endpoint_url):
                self.calls.append((token, endpoint_url))
                return self.record

        for field_name in expected:
            with self.subTest(field=field_name):
                claims = dict(expected)
                claims[field_name] = "attacker-controlled"
                record = SimpleNamespace(
                    **claims,
                    endpoint_url="https://gateway.example.com/runs/run-a/mcp",
                    scopes=("platform:tools",),
                )
                service = TokenService(record)
                verifier = PersistentRunTokenVerifier(
                    token_service=service,
                    resource_server_url="https://gateway.example.com/runs/run-a/mcp",
                    expected_tenant_id=expected["tenant_id"],
                    expected_run_id=expected["run_id"],
                    expected_agent_id=expected["agent_id"],
                    expected_actor_id=expected["actor_id"],
                )
                self.assertIsNone(await verifier.verify_token("opaque-token"))
                self.assertEqual(
                    service.calls,
                    [("opaque-token", "https://gateway.example.com/runs/run-a/mcp")],
                )

    async def test_persistent_token_rejects_missing_scopes_and_endpoint_mismatch(self):
        expected = {
            "tenant_id": "tenant-a",
            "run_id": "run-a",
            "agent_id": "agent-a",
            "actor_id": "actor-a",
        }

        class TokenService:
            def __init__(self, record):
                self.record = record

            async def resolve(self, _token, *, endpoint_url):
                return self.record

        for changes in (
            {"scopes": ()},
            {"endpoint_url": "https://other.example.com/mcp"},
        ):
            record = SimpleNamespace(
                **expected,
                endpoint_url="https://gateway.example.com/runs/run-a/mcp",
                scopes=("platform:tools",),
            )
            for key, value in changes.items():
                setattr(record, key, value)
            verifier = PersistentRunTokenVerifier(
                token_service=TokenService(record),
                resource_server_url="https://gateway.example.com/runs/run-a/mcp",
                expected_tenant_id=expected["tenant_id"],
                expected_run_id=expected["run_id"],
                expected_agent_id=expected["agent_id"],
                expected_actor_id=expected["actor_id"],
            )
            self.assertIsNone(await verifier.verify_token("opaque-token"))
