from __future__ import annotations

import inspect
import unittest

from agent_platform.control_plane.legacy_conversation_adapter import (
    LegacyConversationAdapter,
    LegacyMappingError,
)
from agent_platform.control_plane.sessions_assets import MessageRole


class LegacyConversationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.adapter = LegacyConversationAdapter()

    def test_session_requires_explicit_tenant_and_approved_platform_owner(self):
        row = {"id": "legacy-conv-7", "user_id": "legacy-open-id", "title": "Support"}
        with self.assertRaises(LegacyMappingError):
            self.adapter.adapt_session(row, tenant_id="tenant-a", approved_owner_id="")
        with self.assertRaises(LegacyMappingError):
            self.adapter.adapt_session(row, tenant_id="", approved_owner_id="platform-user-1")

        session = self.adapter.adapt_session(
            row, tenant_id="tenant-a", approved_owner_id="platform-user-1",
        )
        self.assertEqual(session.tenant_id, "tenant-a")
        self.assertEqual(session.owner_id, "platform-user-1")
        self.assertNotEqual(session.owner_id, row["user_id"])

    def test_legacy_project_is_never_inferred_without_approved_mapping(self):
        row = {
            "conversation_id": "legacy-conv-8",
            "owner_id": "legacy-owner",
            "project_id": "legacy-project",
        }
        with self.assertRaisesRegex(LegacyMappingError, "approved project mapping"):
            self.adapter.adapt_session(
                row, tenant_id="tenant-a", approved_owner_id="platform-user-1",
            )

        session = self.adapter.adapt_session(
            row, tenant_id="tenant-a", approved_owner_id="platform-user-1",
            approved_project_id="platform-project-9",
        )
        self.assertEqual(session.project_id, "platform-project-9")

    def test_session_id_is_deterministic_but_tenant_scoped(self):
        row = {"id": "legacy-conv-9", "user_id": "legacy-user"}
        first = self.adapter.adapt_session(
            row, tenant_id="tenant-a", approved_owner_id="platform-user-1",
        )
        retry = self.adapter.adapt_session(
            row, tenant_id="tenant-a", approved_owner_id="platform-user-1",
        )
        other_tenant = self.adapter.adapt_session(
            row, tenant_id="tenant-b", approved_owner_id="platform-user-2",
        )
        self.assertEqual(first.session_id, retry.session_id)
        self.assertNotEqual(first.session_id, other_tenant.session_id)

    def test_message_requires_explicit_actor_and_preserves_source_provenance(self):
        row = {"id": "legacy-message-3", "role": "assistant", "content": "Answer"}
        with self.assertRaises(LegacyMappingError):
            self.adapter.adapt_message(
                row, tenant_id="tenant-a", session_id="session-1", actor_id="",
            )
        message = self.adapter.adapt_message(
            row, tenant_id="tenant-a", session_id="session-1", actor_id="platform-agent-1",
        )
        self.assertEqual(message.role, MessageRole.ASSISTANT)
        self.assertEqual(message.actor_id, "platform-agent-1")
        self.assertEqual(message.metadata["legacy_source"], "conversation")
        self.assertEqual(message.metadata["legacy_id"], "legacy-message-3")

    def test_unknown_legacy_role_is_quarantinable_not_guessed(self):
        with self.assertRaisesRegex(LegacyMappingError, "explicit mapping"):
            self.adapter.adapt_message(
                {"id": "m-1", "role": "bot", "content": "x"},
                tenant_id="tenant-a", session_id="session-1", actor_id="agent-1",
            )

    def test_adapter_has_no_database_or_repository_side_effect_port(self):
        source = inspect.getsource(LegacyConversationAdapter)
        self.assertNotIn("sqlite3", source)
        self.assertNotIn("SessionAssetService", source)
        self.assertNotIn("repository", source.lower())
        self.assertEqual(vars(self.adapter), {})

    def test_docs_table_is_only_a_migration_candidate_not_a_platform_store(self):
        from pathlib import Path

        contract = Path(
            "docs/architecture/c-8-05-resource-scope-and-legacy-boundary.md"
        ).read_text(encoding="utf-8")
        self.assertIn("legacy SQLite `docs` table", contract)
        self.assertIn("not the new platform's document or file-asset store", contract)
        self.assertIn("must not be inserted directly", contract)
        self.assertIn("single-writer cutover", contract)


if __name__ == "__main__":
    unittest.main()
