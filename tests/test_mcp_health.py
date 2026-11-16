from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.tools.mcp_health import (
    InMemoryMCPHealthStore,
    MCPHealthChecker,
)


class FakeSession:
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


class MCPHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_registry_inventory_is_healthy_and_persisted(self):
        store = InMemoryMCPHealthStore()
        checker = MCPHealthChecker(
            store=store,
            session_factory=lambda tenant, server: FakeSession(["search", "read"]),
        )
        result = await checker.check(
            tenant_id="tenant-a", server_id="docs",
            expected_tools=("read", "search"),
        )
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.discovered_tools, ("read", "search"))
        self.assertEqual((await store.get("tenant-a", "docs")).status, "healthy")

    async def test_tool_inventory_mismatch_is_unhealthy(self):
        checker = MCPHealthChecker(
            store=InMemoryMCPHealthStore(),
            session_factory=lambda tenant, server: FakeSession(["search"]),
        )
        result = await checker.check(
            tenant_id="tenant-a", server_id="docs",
            expected_tools=("search", "read"),
        )
        self.assertEqual(result.status, "unhealthy")
        self.assertEqual(result.error_code, "tool_inventory_mismatch")

    async def test_probe_failure_does_not_persist_exception_text(self):
        class Broken:
            async def __aenter__(self):
                raise RuntimeError("https://secret.example Authorization: Bearer secret")
            async def __aexit__(self, *args):
                return None
        checker = MCPHealthChecker(
            store=InMemoryMCPHealthStore(),
            session_factory=lambda tenant, server: Broken(),
        )
        result = await checker.check(
            tenant_id="tenant-a", server_id="docs", expected_tools=("search",),
        )
        self.assertEqual(result.status, "unhealthy")
        self.assertEqual(result.error_code, "probe_RuntimeError")
        self.assertNotIn("secret", result.error_code)


if __name__ == "__main__":
    unittest.main()
