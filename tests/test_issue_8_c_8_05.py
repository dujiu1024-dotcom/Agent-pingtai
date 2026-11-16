from __future__ import annotations

import unittest

from agent_platform.control_plane.legacy_conversation_adapter import (
    LegacyConversationAdapter, LegacyMappingError,
)


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
