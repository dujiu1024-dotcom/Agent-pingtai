from __future__ import annotations

import unittest
from datetime import datetime, timezone

from agent_platform.control_plane.sessions_assets import (
    ContextWindowSnapshot,
    ConversationMessage,
    ConversationSession,
    MessageRole,
    MessageTokenUsage,
    SessionIsolationScope,
)


class SessionDataModelTests(unittest.TestCase):
    def test_session_defaults_to_owner_isolation_and_supports_agent_binding(self):
        session = ConversationSession(
            tenant_id="tenant-a", session_id="session-a", owner_id="user-a",
        )
        self.assertEqual(session.isolation_scope, SessionIsolationScope.USER)
        self.assertIsNone(session.agent_id)

        agent_session = ConversationSession(
            tenant_id="tenant-a", session_id="session-b", owner_id="user-a",
            agent_id="agent-a", isolation_scope=SessionIsolationScope.AGENT,
        )
        self.assertEqual(agent_session.agent_id, "agent-a")

    def test_project_and_agent_scopes_require_their_identity(self):
        with self.assertRaisesRegex(ValueError, "agent_id"):
            ConversationSession(
                tenant_id="tenant-a", session_id="session-a", owner_id="user-a",
                isolation_scope=SessionIsolationScope.AGENT,
            )
        with self.assertRaisesRegex(ValueError, "project_id"):
            ConversationSession(
                tenant_id="tenant-a", session_id="session-b", owner_id="user-a",
                isolation_scope=SessionIsolationScope.PROJECT,
            )

    def test_session_rejects_invalid_status_and_naive_timestamps(self):
        with self.assertRaisesRegex(ValueError, "status"):
            ConversationSession(
                tenant_id="tenant-a", session_id="session-a", owner_id="user-a",
                status="shared",
            )
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            ConversationSession(
                tenant_id="tenant-a", session_id="session-b", owner_id="user-a",
                created_at=datetime(2026, 1, 1),
            )

    def test_message_records_role_timestamp_and_typed_token_usage(self):
        usage = MessageTokenUsage(
            prompt_tokens=12, completion_tokens=8, total_tokens=20, model="model-a",
        )
        message = ConversationMessage(
            tenant_id="tenant-a", message_id="message-a", session_id="session-a",
            role=MessageRole.ASSISTANT, content="answer", actor_id="agent-a",
            token_usage=usage,
        )
        self.assertEqual(message.token_usage.total_tokens, 20)
        self.assertEqual(message.role, MessageRole.ASSISTANT)

    def test_token_usage_rejects_inconsistent_or_negative_counts(self):
        with self.assertRaisesRegex(ValueError, "total_tokens"):
            MessageTokenUsage(prompt_tokens=3, completion_tokens=2, total_tokens=6)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            MessageTokenUsage(prompt_tokens=-1, completion_tokens=2, total_tokens=1)

    def test_context_window_snapshot_is_versioned_and_budget_bounded(self):
        snapshot = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-a",
            version=1, snapshot={"messages": [{"role": "user", "content": "hi"}]},
            token_count=10, max_tokens=100, created_by="user-a",
            run_id="run-a", last_message_id="message-a",
        )
        self.assertEqual(snapshot.version, 1)
        self.assertEqual(snapshot.token_count, 10)

    def test_context_window_snapshot_rejects_invalid_budget_and_version(self):
        base = {
            "tenant_id": "tenant-a", "session_id": "session-a",
            "snapshot_id": "snapshot-a", "version": 1, "snapshot": {},
            "token_count": 101, "max_tokens": 100, "created_by": "user-a",
        }
        with self.assertRaisesRegex(ValueError, "exceeds"):
            ContextWindowSnapshot(**base)
        base["token_count"] = 1
        base["version"] = 0
        with self.assertRaisesRegex(ValueError, "version"):
            ContextWindowSnapshot(**base)

    def test_context_snapshot_requires_timezone_aware_timestamp(self):
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            ContextWindowSnapshot(
                tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-a",
                version=1, snapshot={}, token_count=0, max_tokens=100,
                created_by="user-a", created_at=datetime(2026, 1, 1),
            )


if __name__ == "__main__":
    unittest.main()
