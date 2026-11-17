from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.control_plane.sessions_assets import (
    ConversationSession,
    InMemorySessionRepository,
)
from agent_platform.runtime.execution import LangGraphRunExecutor
from agent_platform.runtime.lifecycle import InMemoryRunStateStore, RunLifecycleManager, RunStatus
from agent_platform.runtime.session_persistence import SessionRunPersistence


class FakeGraph:
    def __init__(self):
        self.checkpointer = object()
        self.calls = []
        self.configs = []
        self.checkpoints = {}

    async def ainvoke(self, payload, *, config):
        self.calls.append(payload)
        self.configs.append(config)
        thread = config["configurable"]["thread_id"]
        self.checkpoints[thread] = "cp-" + str(len(self.calls))
        return {"answer": "persisted answer", "usage": {
            "prompt_tokens": 4, "completion_tokens": 3, "total_tokens": 7,
        }}

    async def aget_state(self, config):
        thread = config["configurable"]["thread_id"]
        checkpoint_id = self.checkpoints.get(thread)
        return SimpleNamespace(
            config={"configurable": {"checkpoint_id": checkpoint_id}},
            values={"messages": [{"role": "assistant", "content": "answer"}]},
            next=(), metadata={"source": "test"},
        )


class StreamingGraph(FakeGraph):
    async def astream(self, payload, *, config, stream_mode):
        self.calls.append(payload)
        self.configs.append(config)
        thread = config["configurable"]["thread_id"]
        self.checkpoints[thread] = "cp-stream-1"
        yield {"messages": [{"role": "user", "content": "hello", "id": "input-1"}]}
        self.checkpoints[thread] = "cp-stream-2"
        yield {"messages": [
            {"role": "user", "content": "hello", "id": "input-1"},
            {"role": "assistant", "content": "partial answer", "id": "assistant-1"},
        ]}


class SessionRunPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.repository = InMemorySessionRepository()
        await self.repository.create_session(ConversationSession(
            tenant_id="tenant-a", session_id="session-a", owner_id="user-a",
        ))
        self.persistence = SessionRunPersistence(self.repository)

    async def test_begin_run_loads_history_and_idempotently_appends_user_message(self):
        first = await self.persistence.begin_run(
            tenant_id="tenant-a", session_id="session-a", run_id="run-a",
            actor_id="user-a", payload={"prompt": "hello"},
        )
        second = await self.persistence.begin_run(
            tenant_id="tenant-a", session_id="session-a", run_id="run-a",
            actor_id="user-a", payload={"prompt": "hello"},
        )
        self.assertEqual(len(first), 1)
        self.assertEqual(first, second)
        rows = await self.repository.list_messages("tenant-a", "session-a")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].content, "hello")

    async def test_executor_loads_history_appends_result_and_persists_checkpoint(self):
        store = InMemoryRunStateStore()
        manager = RunLifecycleManager(store)
        graph = FakeGraph()
        executor = LangGraphRunExecutor(
            graph=graph, lifecycle=manager, timeout_seconds=1,
            session_repository=self.repository,
        )
        await store.create("tenant-a", "run-a")
        result = await executor.execute(
            tenant_id="tenant-a", run_id="run-a", input={"prompt": "hello"},
            session_context={"session_id": "session-a", "actor_id": "user-a"},
        )
        self.assertEqual(result["answer"], "persisted answer")
        configurable = graph.configs[0]["configurable"]
        self.assertEqual(configurable["platform_session_history"][0]["content"], "hello")
        rows = await self.repository.list_messages("tenant-a", "session-a")
        self.assertEqual([row.role.value for row in rows], ["user", "assistant"])
        self.assertEqual(rows[-1].token_usage.total_tokens, 7)
        snapshots = await self.repository.list_context_snapshots("tenant-a", "session-a")
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0].run_id, "run-a")
        self.assertEqual(snapshots[0].snapshot["checkpoint_id"], "cp-1")
        self.assertEqual((await store.get("tenant-a", "run-a")).status, RunStatus.SUCCEEDED)

    async def test_recovery_reuses_user_message_and_resumes_from_graph_checkpoint(self):
        store = InMemoryRunStateStore()
        manager = RunLifecycleManager(store)
        graph = FakeGraph()
        executor = LangGraphRunExecutor(
            graph=graph, lifecycle=manager, timeout_seconds=1,
            session_repository=self.repository,
        )
        await store.create("tenant-a", "run-recover")
        await manager.start("tenant-a", "run-recover")
        await manager.mark_interrupted(
            "tenant-a", "run-recover", checkpoint_ref="cp-previous", reason="worker lost",
        )
        graph.checkpoints["tenant-a:run-recover"] = "cp-previous"
        snapshot = await store.get("tenant-a", "run-recover")
        await executor.resume_claimed(
            snapshot=snapshot, input={"prompt": "continue"},
            session_context={"session_id": "session-a", "actor_id": "user-a"},
        )
        rows = await self.repository.list_messages("tenant-a", "session-a")
        self.assertEqual([row.role.value for row in rows], ["user", "assistant"])
        self.assertEqual(rows[0].content, "continue")
        self.assertIsNone(graph.calls[0])
        self.assertEqual((await store.get("tenant-a", "run-recover")).status, RunStatus.SUCCEEDED)

    async def test_stream_updates_append_messages_and_checkpoint_each_step(self):
        store = InMemoryRunStateStore()
        manager = RunLifecycleManager(store)
        graph = StreamingGraph()
        executor = LangGraphRunExecutor(
            graph=graph, lifecycle=manager, timeout_seconds=1,
            session_repository=self.repository,
        )
        await store.create("tenant-a", "run-stream")
        result = await executor.execute(
            tenant_id="tenant-a", run_id="run-stream", input={"prompt": "hello"},
            session_context={"session_id": "session-a", "actor_id": "user-a"},
        )
        rows = await self.repository.list_messages("tenant-a", "session-a")
        self.assertEqual(
            [item.content for item in rows],
            ["hello", "partial answer"],
        )
        snapshots = await self.repository.list_context_snapshots("tenant-a", "session-a")
        self.assertGreaterEqual(len(snapshots), 2)
        self.assertEqual(snapshots[0].snapshot["checkpoint_id"], "cp-stream-2")
        self.assertEqual(result["messages"][-1]["content"], "partial answer")

    async def test_session_context_without_repository_fails_closed(self):
        store = InMemoryRunStateStore()
        manager = RunLifecycleManager(store)
        executor = LangGraphRunExecutor(graph=FakeGraph(), lifecycle=manager, timeout_seconds=1)
        await store.create("tenant-a", "run-no-repo")
        with self.assertRaisesRegex(ValueError, "session_repository"):
            await executor.execute(
                tenant_id="tenant-a", run_id="run-no-repo", input={"prompt": "hello"},
                session_context={"session_id": "session-a", "actor_id": "user-a"},
            )
        self.assertEqual((await store.get("tenant-a", "run-no-repo")).status, RunStatus.QUEUED)


if __name__ == "__main__":
    unittest.main()
