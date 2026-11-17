from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.control_plane.sessions_assets import (
    InMemoryObjectStorage,
    InMemorySessionRepository,
    MessageRole,
    SessionAssetService,
)
from agent_platform.runtime.session_persistence import SessionRunPersistence


class CheckpointGraph:
    async def aget_state(self, _config):
        return SimpleNamespace(
            config={"configurable": {"checkpoint_id": "checkpoint-run-1"}},
            values={"messages": [{"role": "assistant", "content": "answer"}]},
            next=(),
            metadata={"source": "traceability-test"},
        )


class SessionAssetRunTraceabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_message_file_task_run_and_checkpoint_are_linked(self):
        repository = InMemorySessionRepository()
        service = SessionAssetService(repository, InMemoryObjectStorage())
        session = await service.create_session(
            tenant_id="tenant-a",
            owner_id="user-a",
            project_id="project-a",
        )

        asset = await service.upload_asset(
            tenant_id="tenant-a",
            owner_id="user-a",
            filename="evidence.txt",
            media_type="text/plain",
            content=b"traceable evidence",
            session_id=session.session_id,
        )
        self.assertEqual(asset.session_id, session.session_id)
        self.assertEqual(asset.project_id, "project-a")

        user_message = await service.append_message(
            tenant_id="tenant-a",
            actor_id="user-a",
            session_id=session.session_id,
            role=MessageRole.USER,
            content="Analyze the attached evidence",
            run_id="run-1",
            task_id="task-1",
            asset_ids=(asset.asset_id,),
            metadata={"source": "task_input"},
        )
        persisted_user = await repository.list_messages("tenant-a", session.session_id)
        self.assertEqual(len(persisted_user), 1)
        self.assertEqual(persisted_user[0].message_id, user_message.message_id)
        self.assertEqual(persisted_user[0].session_id, session.session_id)
        self.assertEqual(persisted_user[0].run_id, "run-1")
        self.assertEqual(persisted_user[0].task_id, "task-1")
        self.assertEqual(persisted_user[0].asset_ids, (asset.asset_id,))

        persistence = SessionRunPersistence(repository)
        assistant_message = await persistence.append_assistant(
            tenant_id="tenant-a",
            session_id=session.session_id,
            run_id="run-1",
            actor_id="agent-a",
            result={"answer": "Evidence analyzed"},
        )
        self.assertIsNotNone(assistant_message)
        self.assertEqual(assistant_message.session_id, session.session_id)
        self.assertEqual(assistant_message.run_id, "run-1")

        snapshot = await persistence.checkpoint(
            graph=CheckpointGraph(),
            config={"configurable": {"thread_id": "tenant-a:run-1"}},
            tenant_id="tenant-a",
            session_id=session.session_id,
            run_id="run-1",
            actor_id="agent-a",
        )
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.run_id, "run-1")
        self.assertEqual(snapshot.last_message_id, assistant_message.message_id)

        saved_asset = await repository.get_asset("tenant-a", asset.asset_id)
        saved_messages = await repository.list_messages("tenant-a", session.session_id)
        saved_snapshots = await repository.list_context_snapshots(
            "tenant-a", session.session_id,
        )
        self.assertEqual(saved_asset.session_id, session.session_id)
        self.assertEqual({item.run_id for item in saved_messages}, {"run-1"})
        self.assertEqual(saved_snapshots[0].run_id, "run-1")
        self.assertEqual(saved_snapshots[0].last_message_id, saved_messages[-1].message_id)

    async def test_file_cannot_be_attached_to_a_different_session(self):
        repository = InMemorySessionRepository()
        service = SessionAssetService(repository, InMemoryObjectStorage())
        first = await service.create_session(
            tenant_id="tenant-a", owner_id="user-a",
        )
        second = await service.create_session(
            tenant_id="tenant-a", owner_id="user-a",
        )
        asset = await service.upload_asset(
            tenant_id="tenant-a",
            owner_id="user-a",
            filename="private.txt",
            media_type="text/plain",
            content=b"private",
            session_id=first.session_id,
        )
        with self.assertRaisesRegex(ValueError, "another session"):
            await service.append_message(
                tenant_id="tenant-a",
                actor_id="user-a",
                session_id=second.session_id,
                role=MessageRole.USER,
                content="attempt cross-session attachment",
                asset_ids=(asset.asset_id,),
            )


if __name__ == "__main__":
    unittest.main()
