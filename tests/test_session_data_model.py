from __future__ import annotations

import unittest
from datetime import datetime

from agent_platform.control_plane.sessions_assets import (
    ContextWindowSnapshot,
    ConversationMessage,
    ConversationSession,
    MessageRole,
    InMemorySessionRepository,
    MessageTokenUsage,
    SessionError,
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

    def test_snapshot_payload_must_be_json_compatible(self):
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            ContextWindowSnapshot(
                tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-a",
                version=1, snapshot={"unsupported": object()}, token_count=0,
                max_tokens=100, created_by="user-a",
            )
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            ContextWindowSnapshot(
                tenant_id="tenant-a", session_id="session-a", snapshot={"not_finite": float("nan")},
                snapshot_id="snapshot-b", version=1, token_count=0, max_tokens=100,
                created_by="user-a",
            )

    def test_message_metadata_must_be_json_compatible(self):
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            ConversationMessage(
                tenant_id="tenant-a", message_id="message-a", session_id="session-a",
                role=MessageRole.USER, content="hello", actor_id="user-a",
                metadata={"unsupported": object()},
            )

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


class ContextSnapshotRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemorySessionRepository()
        await self.repository.create_session(ConversationSession(
            tenant_id="tenant-a", session_id="session-a", owner_id="user-a",
        ))

    async def test_message_ids_are_unique_per_tenant(self):
        first = ConversationMessage(
            tenant_id="tenant-a", message_id="message-shared", session_id="session-a",
            role=MessageRole.USER, content="first", actor_id="user-a",
        )
        await self.repository.append_message(first)
        duplicate = ConversationMessage(
            tenant_id="tenant-a", message_id="message-shared", session_id="session-a",
            role=MessageRole.ASSISTANT, content="duplicate", actor_id="agent-a",
        )
        with self.assertRaisesRegex(SessionError, "message already exists"):
            await self.repository.append_message(duplicate)
        await self.repository.create_session(ConversationSession(
            tenant_id="tenant-b", session_id="session-a", owner_id="user-b",
        ))
        other_tenant = ConversationMessage(
            tenant_id="tenant-b", message_id="message-shared", session_id="session-a",
            role=MessageRole.USER, content="isolated", actor_id="user-b",
        )
        await self.repository.append_message(other_tenant)

    async def test_snapshot_round_trip_and_version_order_are_session_scoped(self):
        older = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-1",
            version=1, snapshot={"messages": []}, token_count=0, max_tokens=100,
            created_by="user-a",
        )
        newer = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-2",
            version=2, snapshot={"messages": [{"role": "user", "content": "hi"}]},
            token_count=2, max_tokens=100, created_by="user-a",
        )
        await self.repository.create_context_snapshot(older)
        await self.repository.create_context_snapshot(newer)

        self.assertEqual(
            await self.repository.get_context_snapshot("tenant-a", "session-a", "snapshot-2"),
            newer,
        )
        self.assertEqual(
            await self.repository.list_context_snapshots("tenant-a", "session-a"),
            (newer, older),
        )
        self.assertIsNone(
            await self.repository.get_context_snapshot("tenant-b", "session-a", "snapshot-2")
        )

    async def test_snapshot_rejects_duplicate_id_and_duplicate_session_version(self):
        first = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-1",
            version=1, snapshot={}, token_count=0, max_tokens=100, created_by="user-a",
        )
        await self.repository.create_context_snapshot(first)
        duplicate_version = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-2",
            version=1, snapshot={}, token_count=0, max_tokens=100, created_by="user-a",
        )
        with self.assertRaisesRegex(SessionError, "version already exists"):
            await self.repository.create_context_snapshot(duplicate_version)
        duplicate_id = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-1",
            version=2, snapshot={}, token_count=0, max_tokens=100, created_by="user-a",
        )
        with self.assertRaisesRegex(SessionError, "already exists"):
            await self.repository.create_context_snapshot(duplicate_id)

    async def test_snapshot_ids_are_unique_within_tenant_not_globally(self):
        await self.repository.create_session(ConversationSession(
            tenant_id="tenant-b", session_id="session-a", owner_id="user-b",
        ))
        tenant_a = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="shared-id",
            version=1, snapshot={}, token_count=0, max_tokens=100, created_by="user-a",
        )
        tenant_b = ContextWindowSnapshot(
            tenant_id="tenant-b", session_id="session-a", snapshot_id="shared-id",
            version=1, snapshot={}, token_count=0, max_tokens=100, created_by="user-b",
        )
        await self.repository.create_context_snapshot(tenant_a)
        await self.repository.create_context_snapshot(tenant_b)
        self.assertEqual(
            await self.repository.get_context_snapshot("tenant-b", "session-a", "shared-id"),
            tenant_b,
        )

    async def test_snapshot_message_cursor_must_belong_to_same_tenant_and_session(self):
        await self.repository.append_message(ConversationMessage(
            tenant_id="tenant-a", message_id="message-a", session_id="session-a",
            role=MessageRole.USER, content="hello", actor_id="user-a",
        ))
        valid = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-valid",
            version=1, snapshot={}, token_count=0, max_tokens=100, created_by="user-a",
            last_message_id="message-a",
        )
        await self.repository.create_context_snapshot(valid)
        invalid = ContextWindowSnapshot(
            tenant_id="tenant-a", session_id="session-a", snapshot_id="snapshot-invalid",
            version=2, snapshot={}, token_count=0, max_tokens=100, created_by="user-a",
            last_message_id="message-from-other-session",
        )
        with self.assertRaisesRegex(ValueError, "does not belong"):
            await self.repository.create_context_snapshot(invalid)


if __name__ == "__main__":
    unittest.main()
