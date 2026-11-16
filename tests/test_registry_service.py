from __future__ import annotations

import unittest

from agent_platform.control_plane.registry import (
    InMemoryRegistryRepository,
    RegistryError,
    RegistryService,
)


class RegistryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.repository = InMemoryRegistryRepository()
        self.service = RegistryService(self.repository)

    async def test_create_publish_and_resolve_immutable_snapshot(self) -> None:
        draft = await self.service.create_draft(
            tenant_id="tenant-a", kind="agent", item_id="researcher",
            definition={"model_template": {"id": "general", "version": 1}},
            actor_id="user-a",
        )
        published = await self.service.publish(
            tenant_id="tenant-a", kind="agent", item_id="researcher", version=draft.version,
        )
        active = await self.service.resolve_active(
            tenant_id="tenant-a", kind="agent", item_id="researcher",
        )
        self.assertEqual(published.version, 2)
        self.assertEqual(active.definition, draft.definition)
        self.assertEqual((await self.repository.get("tenant-a", "agent", "researcher", 1)).status, "draft")

    async def test_rollback_creates_new_version_not_mutating_history(self) -> None:
        first = await self.service.create_draft(
            tenant_id="tenant-a", kind="workflow", item_id="flow",
            definition={"steps": ["a"]}, actor_id="user-a",
        )
        first_pub = await self.service.publish(
            tenant_id="tenant-a", kind="workflow", item_id="flow", version=first.version,
        )
        second = await self.service.create_draft(
            tenant_id="tenant-a", kind="workflow", item_id="flow",
            definition={"steps": ["b"]}, actor_id="user-a",
        )
        await self.service.publish(
            tenant_id="tenant-a", kind="workflow", item_id="flow", version=second.version,
        )
        rolled = await self.service.rollback(
            tenant_id="tenant-a", kind="workflow", item_id="flow",
            target_version=first_pub.version,
        )
        self.assertEqual(rolled.version, 5)
        self.assertEqual(rolled.definition, {"steps": ["a"]})

    async def test_reject_embedded_credentials(self) -> None:
        with self.assertRaises(RegistryError):
            await self.service.create_draft(
                tenant_id="tenant-a", kind="tool", item_id="unsafe",
                definition={"config": {"api_key": "plaintext"}}, actor_id="user-a",
            )

    async def test_disable_adds_a_version_and_removes_active_pointer(self) -> None:
        draft = await self.service.create_draft(
            tenant_id="tenant-a", kind="agent", item_id="worker",
            definition={"model_template": {"id": "base", "version": 1}},
            actor_id="user-a",
        )
        await self.service.publish(
            tenant_id="tenant-a", kind="agent", item_id="worker", version=draft.version,
        )
        disabled = await self.service.disable(
            tenant_id="tenant-a", kind="agent", item_id="worker", actor_id="user-a",
        )
        self.assertEqual(disabled.status, "disabled")
        with self.assertRaises(Exception):
            await self.service.resolve_active(
                tenant_id="tenant-a", kind="agent", item_id="worker",
            )

    async def test_reject_camel_case_and_generic_embedded_tokens(self) -> None:
        for key in ("accessToken", "credential", "private-key"):
            with self.subTest(key=key):
                with self.assertRaises(RegistryError):
                    await self.service.create_draft(
                        tenant_id="tenant-a", kind="tool", item_id="unsafe",
                        definition={key: "plaintext"}, actor_id="user-a",
                    )

    async def test_tenant_isolation(self) -> None:
        await self.service.create_draft(
            tenant_id="tenant-a", kind="skill", item_id="summarize",
            definition={"entrypoint": "SKILL.md"}, actor_id="user-a",
        )
        with self.assertRaises(Exception):
            await self.service.resolve_active(
                tenant_id="tenant-b", kind="skill", item_id="summarize",
            )


if __name__ == "__main__":
    unittest.main()
