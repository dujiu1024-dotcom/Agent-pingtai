from __future__ import annotations

import unittest

from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.workflows.expert_registry import ControlPlaneExpertRegistry


class ControlPlaneExpertRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolves_only_published_active_agent_metadata(self):
        service = RegistryService(InMemoryRegistryRepository())
        adapter = ControlPlaneExpertRegistry(
            registry_service=service, tenant_id="tenant-a",
        )
        self.assertIsNone(await adapter.get("researcher"))

        draft = await service.create_draft(
            tenant_id="tenant-a", kind="agent", item_id="researcher",
            definition={
                "name": "Researcher",
                "description": "Finds and verifies source material",
                "capabilities": ["market_research", "source_review"],
            },
            actor_id="admin",
        )
        self.assertIsNone(await adapter.get("researcher"))
        await service.publish(
            tenant_id="tenant-a", kind="agent", item_id="researcher",
            version=draft.version,
        )
        profile = await adapter.get("researcher")
        self.assertEqual(profile.expert_id, "researcher")
        self.assertEqual(profile.name, "Researcher")
        self.assertEqual(profile.capabilities, frozenset({"market_research", "source_review"}))
        self.assertTrue(profile.enabled)

    async def test_rejects_malformed_published_capabilities(self):
        service = RegistryService(InMemoryRegistryRepository())
        adapter = ControlPlaneExpertRegistry(
            registry_service=service, tenant_id="tenant-a",
        )
        draft = await service.create_draft(
            tenant_id="tenant-a", kind="agent", item_id="bad-agent",
            definition={"capabilities": "all"},
            actor_id="admin",
        )
        await service.publish(
            tenant_id="tenant-a", kind="agent", item_id="bad-agent",
            version=draft.version,
        )
        with self.assertRaisesRegex(ValueError, "capabilities must be an array"):
            await adapter.get("bad-agent")

    async def test_registry_lookup_is_tenant_scoped(self):
        service = RegistryService(InMemoryRegistryRepository())
        draft = await service.create_draft(
            tenant_id="tenant-b", kind="agent", item_id="researcher",
            definition={"capabilities": ["research"]}, actor_id="admin",
        )
        await service.publish(
            tenant_id="tenant-b", kind="agent", item_id="researcher",
            version=draft.version,
        )
        adapter = ControlPlaneExpertRegistry(
            registry_service=service, tenant_id="tenant-a",
        )
        self.assertIsNone(await adapter.get("researcher"))


if __name__ == "__main__":
    unittest.main()
