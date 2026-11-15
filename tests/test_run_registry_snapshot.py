from __future__ import annotations

import unittest

from agent_platform.control_plane.registry import InMemoryRegistryRepository, RegistryService
from agent_platform.runtime.registry_snapshot import (
    InMemoryRunSnapshotStore, RunRegistrySnapshotService,
)


class RunRegistrySnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.registry_repo = InMemoryRegistryRepository()
        self.registry = RegistryService(self.registry_repo)
        self.store = InMemoryRunSnapshotStore()
        self.snapshots = RunRegistrySnapshotService(registry=self.registry, store=self.store)

    async def publish(self, kind, item_id, definition):
        draft = await self.registry.create_draft(
            tenant_id="tenant-a", kind=kind, item_id=item_id,
            definition=definition, actor_id="admin",
        )
        return await self.registry.publish(
            tenant_id="tenant-a", kind=kind, item_id=item_id, version=draft.version,
        )

    async def test_snapshot_pins_versions_and_is_immutable(self):
        first = await self.publish("agent", "researcher", {"prompt": "v1"})
        await self.publish("model_template", "general", {"model": "m1"})
        snapshot = await self.snapshots.pin(
            tenant_id="tenant-a", run_id="run-1",
            references=(("agent", "researcher"), ("model_template", "general")),
        )
        self.assertEqual(len(snapshot.entries), 2)
        self.assertEqual(
            next(e for e in snapshot.entries if e["kind"] == "agent")["version"],
            first.version,
        )
        await self.publish("agent", "researcher", {"prompt": "v2"})
        loaded = await self.snapshots.get(tenant_id="tenant-a", run_id="run-1")
        self.assertEqual(loaded.digest, snapshot.digest)
        with self.assertRaises(ValueError):
            await self.snapshots.pin(
                tenant_id="tenant-a", run_id="run-1",
                references=(("agent", "researcher"),),
            )

    async def test_snapshot_tenant_isolation(self):
        await self.publish("agent", "researcher", {"prompt": "v1"})
        with self.assertRaises(Exception):
            await self.snapshots.pin(
                tenant_id="tenant-b", run_id="run-2",
                references=(("agent", "researcher"),),
            )


if __name__ == "__main__":
    unittest.main()
