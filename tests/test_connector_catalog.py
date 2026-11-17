from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.tools.connector_catalog import (
    ConnectorCatalog,
    ConnectorCatalogError,
    ConnectorManifest,
    ConnectorManifestError,
    InMemoryConnectorManifestRegistry,
    MCPRegistryManifestSource,
)
from agent_platform.tools.mcp_health import InMemoryMCPHealthStore, MCPHealthChecker


def manifest(connector_id="docs"):
    return {
        "connector_id": connector_id,
        "name": "Documents",
        "version": "1.2.0",
        "description": "Document connector",
        "auth_type": "oauth",
        "operations": [
            {
                "name": "search",
                "description": "Search documents",
                "input_schema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            },
            {
                "name": "read",
                "description": "Read a document",
                "input_schema": {
                    "type": "object",
                    "properties": {"document_id": {"type": "string"}},
                    "required": ["document_id"],
                    "additionalProperties": False,
                },
            },
        ],
    }


class FakeConnector:
    def __init__(self):
        self.started = 0
        self.closed = 0

    async def start(self):
        self.started += 1

    async def close(self):
        self.closed += 1


class FakeHealthSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def ping(self):
        return True

    async def list_tools(self):
        return SimpleNamespace(tools=[
            SimpleNamespace(name="search"), SimpleNamespace(name="read")
        ])


class ConnectorCatalogTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry = InMemoryConnectorManifestRegistry()
        self.created = []
        self.health = MCPHealthChecker(
            store=InMemoryMCPHealthStore(),
            session_factory=lambda tenant, connector: FakeHealthSession(),
        )
        self.catalog = ConnectorCatalog(
            registry=self.registry,
            connector_factory=lambda _manifest: self._new_connector(),
            health_checker=self.health,
        )

    def _new_connector(self):
        connector = FakeConnector()
        self.created.append(connector)
        return connector

    async def test_manifest_validation_and_tenant_scoped_registry(self):
        parsed = await self.catalog.register(
            tenant_id="tenant-a", manifest=manifest(),
        )
        self.assertEqual(parsed.operations[0].name, "read")
        self.assertEqual(len(await self.catalog.list_manifests(tenant_id="tenant-a")), 1)
        self.assertEqual(await self.catalog.list_manifests(tenant_id="tenant-b"), ())
        with self.assertRaises(ConnectorManifestError):
            await self.catalog.register(
                tenant_id="tenant-a",
                manifest={**manifest(), "command": ["curl", "evil"]},
            )
        with self.assertRaises(ConnectorManifestError):
            await self.catalog.register(
                tenant_id="tenant-a",
                manifest={**manifest(), "auth_type": "custom"},
            )

    async def test_load_is_lazy_cached_and_unload_closes_connector(self):
        await self.catalog.register(tenant_id="tenant-a", manifest=manifest())
        self.assertEqual(self.created, [])
        first = await self.catalog.load(tenant_id="tenant-a", connector_id="docs")
        second = await self.catalog.load(tenant_id="tenant-a", connector_id="docs")
        self.assertIs(first, second)
        self.assertEqual(first.started, 1)
        self.assertEqual(await self.catalog.loaded_ids(tenant_id="tenant-a"), ("docs",))
        self.assertTrue(await self.catalog.unload(tenant_id="tenant-a", connector_id="docs"))
        self.assertEqual(first.closed, 1)
        self.assertFalse(await self.catalog.unload(tenant_id="tenant-a", connector_id="docs"))

    async def test_concurrent_load_creates_only_one_connector(self):
        await self.catalog.register(tenant_id="tenant-a", manifest=manifest())
        loaded = await __import__("asyncio").gather(*[
            self.catalog.load(tenant_id="tenant-a", connector_id="docs")
            for _ in range(8)
        ])
        self.assertTrue(all(item is loaded[0] for item in loaded))
        self.assertEqual(len(self.created), 1)

    async def test_load_failure_is_not_cached_and_can_retry(self):
        class Failing:
            async def start(self):
                raise RuntimeError("startup failed")

        attempts = []
        def factory(_manifest):
            attempts.append(1)
            return Failing() if len(attempts) == 1 else FakeConnector()

        catalog = ConnectorCatalog(registry=self.registry, connector_factory=factory)
        await catalog.register(tenant_id="tenant-a", manifest=manifest())
        with self.assertRaisesRegex(RuntimeError, "startup failed"):
            await catalog.load(tenant_id="tenant-a", connector_id="docs")
        self.assertEqual(await catalog.loaded_ids(tenant_id="tenant-a"), ())
        self.assertIsInstance(
            await catalog.load(tenant_id="tenant-a", connector_id="docs"), FakeConnector
        )

    async def test_health_check_reuses_mcp_checker_and_manifest_inventory(self):
        await self.catalog.register(tenant_id="tenant-a", manifest=manifest())
        with self.assertRaises(ConnectorCatalogError):
            await self.catalog.health_check(tenant_id="tenant-a", connector_id="docs")
        await self.catalog.load(tenant_id="tenant-a", connector_id="docs")
        result = await self.catalog.health_check(tenant_id="tenant-a", connector_id="docs")
        self.assertEqual(result.status, "healthy")
        self.assertEqual(result.expected_tools, ("read", "search"))

    async def test_registry_refresh_imports_normalized_official_envelopes(self):
        class RegistryClient:
            async def list_servers(self):
                return [{"server": {"platform_manifest": manifest("mcp-docs")}}]

        imported = await self.catalog.refresh(
            tenant_id="tenant-a",
            source=MCPRegistryManifestSource(RegistryClient()),
        )
        self.assertEqual(imported[0].connector_id, "mcp-docs")
        self.assertEqual(imported[0].source, "mcp-registry")

    async def test_manifest_rejects_executable_operation_fields(self):
        unsafe = manifest()
        unsafe["operations"][0]["url"] = "https://untrusted.invalid"
        with self.assertRaises(ConnectorManifestError):
            ConnectorManifest.from_mapping(unsafe)


if __name__ == "__main__":
    unittest.main()
