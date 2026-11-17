from __future__ import annotations

import unittest

from agent_platform.control_plane.data_connectors import (
    DataConnection,
    InMemoryConnectorRepository,
)
from agent_platform.control_plane.legacy_conversation_adapter import (
    LegacyConversationAdapter,
    LegacyMappingError,
)
from agent_platform.control_plane.sessions_assets import (
    AssetStatus,
    ConversationSession,
    FileAsset,
    InMemorySessionRepository,
)


class ProjectIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_and_asset_lists_are_project_filtered(self):
        repo = InMemorySessionRepository()
        await repo.create_session(ConversationSession(
            tenant_id="tenant-a", session_id="s-a", owner_id="user-a",
            project_id="project-a",
        ))
        await repo.create_session(ConversationSession(
            tenant_id="tenant-a", session_id="s-b", owner_id="user-a",
            project_id="project-b",
        ))
        for asset_id, project_id in (("f-a", "project-a"), ("f-b", "project-b")):
            await repo.create_asset(FileAsset(
                tenant_id="tenant-a", asset_id=asset_id, owner_id="user-a",
                object_key=f"tenant/tenant-a/{asset_id}", filename=f"{asset_id}.txt",
                media_type="text/plain", size_bytes=1, sha256="0" * 64,
                status=AssetStatus.AVAILABLE, project_id=project_id,
            ))

        sessions = await repo.list_sessions("tenant-a", "user-a", 100, "project-a")
        assets = await repo.list_assets("tenant-a", "user-a", None, 100, "project-a")
        self.assertEqual([row.session_id for row in sessions], ["s-a"])
        self.assertEqual([row.asset_id for row in assets], ["f-a"])

    async def test_connector_project_binding_is_retained_in_repository(self):
        repo = InMemoryConnectorRepository()
        connection = DataConnection(
            tenant_id="tenant-a", connection_id="drive-a", provider="drive",
            secret_ref="secret://tenant-a/drive-a", scopes=("files.read",),
            project_id="project-a",
        )
        await repo.create(connection, created_by="user-a")
        stored = await repo.get("tenant-a", "drive-a")
        self.assertEqual(stored.project_id, "project-a")
        self.assertIsNone(await repo.get("tenant-b", "drive-a"))


class LegacyConversationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = LegacyConversationAdapter()

    def test_session_mapping_is_explicit_and_deterministic(self):
        row = {"conversation_id": "legacy-17", "owner_id": "user-1", "project_id": "old-project"}
        with self.assertRaises(LegacyMappingError):
            self.adapter.adapt_session(row, tenant_id="tenant-1")
        first = self.adapter.adapt_session(
            row, tenant_id="tenant-1", approved_project_id="project-9",
        )
        second = self.adapter.adapt_session(
            row, tenant_id="tenant-1", approved_project_id="project-9",
        )
        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(first.project_id, "project-9")
        self.assertNotEqual(first.session_id, self.adapter.adapt_session(
            row, tenant_id="tenant-2", approved_project_id="project-9",
        ).session_id)

    def test_message_adapter_preserves_provenance_without_writing(self):
        message = self.adapter.adapt_message(
            {"message_id": "legacy-msg-3", "role": "user", "content": "hello"},
            tenant_id="tenant-1", session_id="session-1", actor_id="user-1",
        )
        self.assertEqual(message.metadata["legacy_source"], "conversation")
        self.assertEqual(message.metadata["legacy_id"], "legacy-msg-3")
        with self.assertRaises(LegacyMappingError):
            self.adapter.adapt_message(
                {"message_id": "legacy-msg-4", "role": "unknown", "content": "hello"},
                tenant_id="tenant-1", session_id="session-1", actor_id="user-1",
            )


if __name__ == "__main__":
    unittest.main()
