from __future__ import annotations

import unittest
from typing import Any

from agent_platform.switching.gateway import (
    CutoverGateway,
    TenantCutoverPolicy,
    select_workflow_runtime,
)


class TenantCutoverPolicyTests(unittest.TestCase):
    def policy(self, **overrides: Any) -> TenantCutoverPolicy:
        values: dict[str, Any] = {
            "enabled": True,
            "tenant_allowlist": frozenset({"tenant-allowlisted"}),
            "traffic_percentage": 0,
            "salt": b"stable-test-salt-0001",
        }
        values.update(overrides)
        return TenantCutoverPolicy(**values)

    def test_disabled_policy_keeps_all_tenants_on_legacy(self) -> None:
        policy = self.policy(enabled=False, traffic_percentage=100)
        self.assertFalse(policy.use_new_system("tenant-allowlisted"))
        self.assertFalse(policy.use_new_system("tenant-any"))

    def test_allowlist_routes_to_new_system_at_zero_percentage(self) -> None:
        policy = self.policy()
        self.assertTrue(policy.use_new_system("tenant-allowlisted"))
        self.assertFalse(policy.use_new_system("tenant-other"))

    def test_percentage_zero_and_one_hundred_have_exact_boundaries(self) -> None:
        self.assertFalse(self.policy(tenant_allowlist=frozenset()).use_new_system("t-1"))
        self.assertTrue(self.policy(
            tenant_allowlist=frozenset(), traffic_percentage=100,
        ).use_new_system("t-1"))

    def test_percentage_assignment_is_stable_and_salt_scoped(self) -> None:
        first = self.policy(tenant_allowlist=frozenset(), traffic_percentage=37)
        second = self.policy(tenant_allowlist=frozenset(), traffic_percentage=37)
        decisions = {f"tenant-{index}": first.use_new_system(f"tenant-{index}") for index in range(250)}
        self.assertEqual(decisions, {
            tenant: second.use_new_system(tenant) for tenant in decisions
        })
        self.assertGreater(sum(decisions.values()), 40)
        self.assertLess(sum(decisions.values()), 145)

    def test_invalid_percentage_and_salt_are_rejected(self) -> None:
        for value in (True, -1, 101, 1.5, "50"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.policy(traffic_percentage=value)
        with self.assertRaises(ValueError):
            self.policy(salt=b"short")

    def test_environment_configuration_is_explicit_and_validated(self) -> None:
        policy = TenantCutoverPolicy.from_env({
            "AGENT_PLATFORM_CUTOVER_ENABLED": "true",
            "AGENT_PLATFORM_CUTOVER_TENANTS": "tenant-a, tenant-b,,",
            "AGENT_PLATFORM_CUTOVER_PERCENTAGE": "25",
            "AGENT_PLATFORM_CUTOVER_SALT": "deployment-secret-123",
        })
        self.assertEqual(policy.tenant_allowlist, frozenset({"tenant-a", "tenant-b"}))
        self.assertEqual(policy.traffic_percentage, 25)
        with self.assertRaises(ValueError):
            TenantCutoverPolicy.from_env({
                "AGENT_PLATFORM_CUTOVER_ENABLED": "sometimes",
            })
        with self.assertRaises(ValueError):
            TenantCutoverPolicy.from_env({
                "AGENT_PLATFORM_CUTOVER_ENABLED": "true",
                "AGENT_PLATFORM_CUTOVER_PERCENTAGE": "20",
            })

    def test_workflow_entry_uses_same_tenant_policy(self) -> None:
        policy = self.policy()
        new_runtime, legacy_runtime = object(), object()
        self.assertIs(
            select_workflow_runtime(
                "tenant-allowlisted", policy,
                new_runtime=new_runtime, legacy_runtime=legacy_runtime,
            ),
            new_runtime,
        )
        self.assertIs(
            select_workflow_runtime(
                "tenant-other", policy,
                new_runtime=new_runtime, legacy_runtime=legacy_runtime,
            ),
            legacy_runtime,
        )


class CutoverGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_gateway_routes_allowlisted_and_non_canary_tenants(self) -> None:
        seen: list[str] = []

        def app(name: str):
            async def invoke(scope: dict[str, Any], receive: Any, send: Any) -> None:
                seen.append(name)
            return invoke

        async def resolve(scope: dict[str, Any]) -> str | None:
            return scope.get("authenticated_tenant")

        gateway = CutoverGateway(
            new_app=app("new"),
            legacy_app=app("legacy"),
            policy=TenantCutoverPolicy(
                enabled=True,
                tenant_allowlist=frozenset({"tenant-a"}),
                traffic_percentage=0,
                salt=b"stable-test-salt-0001",
            ),
            tenant_resolver=resolve,
        )
        async def unused_receive() -> dict[str, Any]:
            return {"type": "http.request"}
        async def unused_send(message: dict[str, Any]) -> None:
            return None

        await gateway(
            {"type": "http", "authenticated_tenant": "tenant-a", "headers": []},
            unused_receive, unused_send,
        )
        await gateway(
            {"type": "http", "authenticated_tenant": "tenant-b", "headers": []},
            unused_receive, unused_send,
        )
        await gateway(
            {"type": "http", "headers": [(b"x-tenant-id", b"tenant-a")]},
            unused_receive, unused_send,
        )
        self.assertEqual(seen, ["new", "legacy", "legacy"])

    async def test_resolver_failure_returns_503_without_dispatch(self) -> None:
        called: list[str] = []

        async def app(scope: Any, receive: Any, send: Any) -> None:
            called.append("app")

        async def resolve(scope: Any) -> str | None:
            raise RuntimeError("identity backend unavailable")

        messages: list[dict[str, Any]] = []
        async def send(message: dict[str, Any]) -> None:
            messages.append(message)

        gateway = CutoverGateway(
            new_app=app,
            legacy_app=app,
            policy=TenantCutoverPolicy(
                enabled=True, tenant_allowlist=frozenset(), traffic_percentage=100,
                salt=b"stable-test-salt-0001",
            ),
            tenant_resolver=resolve,
        )
        async def receive() -> dict[str, Any]:
            return {"type": "http.request"}
        await gateway({"type": "http"}, receive, send)
        self.assertEqual(called, [])
        self.assertEqual(messages[0]["status"], 503)
        self.assertIn(b"CUTOVER_IDENTITY_UNAVAILABLE", messages[1]["body"])

    async def test_non_http_protocols_pass_to_new_app(self) -> None:
        seen: list[str] = []
        async def new_app(scope: Any, receive: Any, send: Any) -> None:
            seen.append("new")
        async def legacy_app(scope: Any, receive: Any, send: Any) -> None:
            seen.append("legacy")
        async def resolve(scope: Any) -> str | None:
            raise AssertionError("resolver must not run for lifespan")
        gateway = CutoverGateway(
            new_app=new_app, legacy_app=legacy_app,
            policy=TenantCutoverPolicy(
                enabled=True, tenant_allowlist=frozenset(), traffic_percentage=0,
                salt=b"stable-test-salt-0001",
            ),
            tenant_resolver=resolve,
        )
        async def receive() -> dict[str, Any]:
            return {"type": "lifespan.startup"}
        async def send(message: Any) -> None:
            return None
        await gateway({"type": "lifespan"}, receive, send)
        self.assertEqual(seen, ["new"])


if __name__ == "__main__":
    unittest.main()
