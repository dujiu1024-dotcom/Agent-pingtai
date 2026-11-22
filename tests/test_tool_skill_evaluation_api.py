from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.contract import ApiV1CompatibilityMiddleware
from agent_platform.api.tool_skill_evaluation import create_tool_skill_evaluation_router
from agent_platform.control_plane.registry import (
    InMemoryRegistryRepository,
    RegistryService,
    RegistryVersion,
)
from agent_platform.control_plane.skill_dependency_review import (
    InMemorySkillDependencyReviewStore,
    SkillDependencyReviewer,
)
from agent_platform.identity.models import Principal
from agent_platform.tools.mcp_health import InMemoryMCPHealthStore, MCPHealthChecker


class Session:
    def __init__(self, names):
        self.names = names

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def ping(self):
        return True

    async def list_tools(self):
        return SimpleNamespace(tools=[SimpleNamespace(name=name) for name in self.names])


class Audit:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    async def record(self, **event):
        if self.fail:
            raise RuntimeError("audit database unavailable")
        self.events.append(event)


class Scanner:
    async def scan(self, package, pin):
        return ()


class ToolSkillEvaluationApiTests(unittest.TestCase):
    def setUp(self):
        self.repository = InMemoryRegistryRepository()
        self.registry = RegistryService(self.repository)
        self.audit = Audit()
        self.allowed = True
        self.version_allowed = True
        self.probe_calls = []

        async def principal(request):
            # In local/no-auth deployments this is the host-injected local principal.
            tenant = request.headers.get("x-tenant", "tenant-a")
            return Principal(
                user_id="user-a", tenant_id=tenant, session_id="local-session",
                email="user@example.test", display_name="Local User",
            )

        async def resolve_registry(actor):
            return self.registry

        async def authorize(actor, kind, item_id, action):
            return self.allowed and kind in {"tool", "skill"}

        def checker(actor):
            def factory(tenant_id, server_id):
                self.probe_calls.append((tenant_id, server_id))
                return Session(["search", "read"])
            return MCPHealthChecker(store=InMemoryMCPHealthStore(), session_factory=factory)

        def reviewer(actor):
            return SkillDependencyReviewer(
                store=InMemorySkillDependencyReviewStore(),
                approved_pins={"safe-parser": ("==1.2.3",)},
                scanner=Scanner(),
            )

        app = FastAPI()
        app.add_middleware(ApiV1CompatibilityMiddleware)
        app.include_router(create_tool_skill_evaluation_router(
            resolve_principal=principal,
            resolve_registry=resolve_registry,
            authorize=authorize,
            authorize_version=lambda actor, kind, item_id, version, action: self.version_allowed,
            resolve_mcp_checker=checker,
            resolve_skill_reviewer=reviewer,
            audit_writer=self.audit,
        ))
        self.client = TestClient(app)

    def _publish(self, kind, item_id, definition, tenant="tenant-a"):
        item = RegistryVersion(
            tenant_id=tenant, kind=kind, item_id=item_id, version=2,
            definition=definition, status="published", created_by="admin",
            created_at=datetime.now(timezone.utc), published_at=datetime.now(timezone.utc),
        )
        import asyncio
        asyncio.run(self.repository.put(item, expected_latest=None))
        return item

    def test_tool_connection_test_uses_host_session_and_persists_audit(self):
        self._publish("tool", "mcp-docs", {"allowlist": ["read", "search"]})
        response = self.client.post(
            "/api/v1/tools/mcp-docs/test-connection", json={"version": 2},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "healthy")
        self.assertEqual(response.json()["discovered_tools"], ["read", "search"])
        self.assertEqual(self.probe_calls, [("tenant-a", "mcp-docs")])
        self.assertEqual([event["outcome"] for event in self.audit.events], ["started", "succeeded"])

    def test_skill_evaluation_is_version_pinned_and_audited(self):
        self._publish("skill", "summarize", {
            "manifest": {"name": "summarize", "dependencies": {"safe-parser": "==1.2.3"}},
        })
        response = self.client.post(
            "/api/v1/skills/summarize/evaluate", json={"version": 2},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "approved")
        self.assertEqual(response.json()["skill_version"], 2)
        self.assertEqual([event["outcome"] for event in self.audit.events], ["started", "succeeded"])

    def test_skill_evaluation_reports_scanner_unavailable_as_rejected(self):
        self._publish("skill", "scanner-offline", {
            "manifest": {
                "name": "scanner-offline",
                "dependencies": {"safe-parser": "==1.2.3"},
            },
        })
        app = FastAPI()
        app.add_middleware(ApiV1CompatibilityMiddleware)
        app.include_router(create_tool_skill_evaluation_router(
            resolve_principal=lambda request: Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="s",
                email="user@example.test", display_name="User",
            ),
            resolve_registry=lambda actor: self.registry,
            authorize=lambda *args: True,
            authorize_version=lambda *args: True,
            resolve_mcp_checker=lambda actor: None,
            resolve_skill_reviewer=lambda actor: SkillDependencyReviewer(
                store=InMemorySkillDependencyReviewStore(),
                approved_pins={"safe-parser": ("==1.2.3",)},
                scanner=None,
            ),
            audit_writer=self.audit,
        ))
        response = TestClient(app).post(
            "/api/v1/skills/scanner-offline/evaluate", json={"version": 2},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "rejected")
        self.assertTrue(any(
            "dependency_scanner_not_configured" in finding
            for finding in response.json()["findings"]
        ))

    def test_missing_principal_is_rejected(self):
        app = FastAPI()
        app.add_middleware(ApiV1CompatibilityMiddleware)

        async def missing(request):
            return None
        app.include_router(create_tool_skill_evaluation_router(
            resolve_principal=missing, resolve_registry=lambda actor: self.registry,
            authorize=lambda *args: True,
            authorize_version=lambda *args: True,
            resolve_mcp_checker=lambda actor: None,
            resolve_skill_reviewer=lambda actor: None,
            audit_writer=self.audit,
        ))
        response = TestClient(app).post(
            "/api/v1/tools/unknown/test-connection", json={"version": 1},
        )
        self.assertEqual(response.status_code, 401)

    def test_cross_tenant_resource_is_not_found(self):
        self._publish("tool", "tenant-tool", {"allowlist": ["read"]})
        response = self.client.post(
            "/api/v1/tools/tenant-tool/test-connection",
            json={"version": 2}, headers={"x-tenant": "tenant-b"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.probe_calls, [])

    def test_permission_denial_is_audited_without_running_probe(self):
        self._publish("tool", "denied-tool", {"allowlist": ["read"]})
        self.allowed = False
        response = self.client.post(
            "/api/v1/tools/denied-tool/test-connection", json={"version": 2},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.probe_calls, [])
        self.assertEqual([event["outcome"] for event in self.audit.events], ["denied"])

    def test_caller_cannot_override_endpoint_or_credentials(self):
        self._publish("tool", "no-override", {"allowlist": ["read"]})
        response = self.client.post(
            "/api/v1/tools/no-override/test-connection",
            json={"version": 2, "endpoint": "https://attacker.invalid", "secret": "do-not-use"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.probe_calls, [])

    def test_version_permission_denial_is_audited_without_running_probe(self):
        self._publish("tool", "version-denied", {"allowlist": ["read"]})
        self.version_allowed = False
        response = self.client.post(
            "/api/v1/tools/version-denied/test-connection", json={"version": 2},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.probe_calls, [])
        self.assertEqual([event["outcome"] for event in self.audit.events], ["denied"])

    def test_audit_failure_fails_closed_before_probe(self):
        self._publish("tool", "audit-tool", {"allowlist": ["read"]})
        self.audit.fail = True
        response = self.client.post(
            "/api/v1/tools/audit-tool/test-connection", json={"version": 2},
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(self.probe_calls, [])

    def test_timeout_auth_and_inventory_mismatch_have_stable_codes(self):
        self._publish("tool", "error-codes", {"allowlist": ["read"]})

        class SlowSession(Session):
            async def ping(self):
                await asyncio.sleep(0.02)
                return True

        class AuthenticationError(Exception):
            pass

        class AuthSession(Session):
            async def ping(self):
                raise AuthenticationError("invalid credentials")

        def client_for(session, timeout=0.5):
            app = FastAPI()
            app.add_middleware(ApiV1CompatibilityMiddleware)
            app.include_router(create_tool_skill_evaluation_router(
                resolve_principal=lambda request: Principal(
                    user_id="user-a", tenant_id="tenant-a", session_id="s",
                    email="user@example.test", display_name="User",
                ),
                resolve_registry=lambda actor: self.registry,
                authorize=lambda *args: True,
                authorize_version=lambda *args: True,
                resolve_mcp_checker=lambda actor: MCPHealthChecker(
                    store=InMemoryMCPHealthStore(),
                    session_factory=lambda tenant, server: session,
                    timeout_seconds=timeout,
                ),
                resolve_skill_reviewer=lambda actor: None,
                audit_writer=Audit(),
            ))
            return TestClient(app)

        timeout_result = client_for(SlowSession(["read"]), timeout=0.001).post(
            "/api/v1/tools/error-codes/test-connection", json={"version": 2},
        )
        self.assertEqual(timeout_result.status_code, 200)
        self.assertEqual(timeout_result.json()["error_code"], "probe_timeout")

        auth_result = client_for(AuthSession(["read"])).post(
            "/api/v1/tools/error-codes/test-connection", json={"version": 2},
        )
        self.assertEqual(auth_result.status_code, 200)
        self.assertEqual(auth_result.json()["error_code"], "authentication_failed")

        mismatch_result = client_for(Session(["write"])).post(
            "/api/v1/tools/error-codes/test-connection", json={"version": 2},
        )
        self.assertEqual(mismatch_result.status_code, 200)
        self.assertEqual(mismatch_result.json()["error_code"], "tool_inventory_mismatch")

    def test_secret_bearing_probe_exception_is_not_returned_or_audited(self):
        self._publish("tool", "safe-error", {"allowlist": ["read"]})

        def broken_checker(actor):
            class BrokenSession:
                async def __aenter__(self):
                    raise RuntimeError("https://private.invalid Authorization: Bearer TOP_SECRET")
                async def __aexit__(self, *args):
                    return None
            return MCPHealthChecker(
                store=InMemoryMCPHealthStore(),
                session_factory=lambda tenant, server: BrokenSession(),
            )
        app = FastAPI()
        app.add_middleware(ApiV1CompatibilityMiddleware)
        app.include_router(create_tool_skill_evaluation_router(
            resolve_principal=lambda request: Principal(
                user_id="user-a", tenant_id="tenant-a", session_id="s",
                email="user@example.test", display_name="User",
            ),
            resolve_registry=lambda actor: self.registry,
            authorize=lambda *args: True,
            authorize_version=lambda *args: True,
            resolve_mcp_checker=broken_checker,
            resolve_skill_reviewer=lambda actor: None,
            audit_writer=self.audit,
        ))
        response = TestClient(app).post(
            "/api/v1/tools/safe-error/test-connection", json={"version": 2},
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("TOP_SECRET", response.text)
        self.assertNotIn("private.invalid", response.text)
        self.assertNotIn("TOP_SECRET", str(self.audit.events))


if __name__ == "__main__":
    unittest.main()
