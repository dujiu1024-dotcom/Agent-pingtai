"""A-25-01 acceptance: a restarted Run resumes its checkpoint without token drift."""
from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from agent_platform.control_plane.sessions_assets import (
    ConversationSession,
    InMemorySessionRepository,
)
from agent_platform.runtime.execution import LangGraphRunExecutor
from agent_platform.runtime.lifecycle import (
    InMemoryRunStateStore,
    RunLifecycleManager,
    RunStatus,
)


class SharedCheckpoint:
    """Small durable-checkpoint stand-in shared by two worker graph instances."""

    def __init__(self):
        self.checkpoint_id = None
        self.values = {}


class CrashingWorkerGraph:
    def __init__(self, checkpoint, reached_checkpoint, hold_worker):
        self.checkpointer = object()
        self.checkpoint = checkpoint
        self.reached_checkpoint = reached_checkpoint
        self.hold_worker = hold_worker
        self.calls = []

    async def astream(self, payload, *, config, stream_mode):
        self.calls.append((payload, stream_mode))
        self.checkpoint.checkpoint_id = "checkpoint-after-partial-response"
        self.checkpoint.values = {
            "messages": [
                {"role": "user", "content": "calculate 1 + 1", "id": "user-message"},
                {"role": "assistant", "content": "working", "id": "partial-message"},
            ]
        }
        yield self.checkpoint.values
        self.reached_checkpoint.set()
        await self.hold_worker.wait()

    async def aget_state(self, config):
        return SimpleNamespace(
            config={"configurable": {"checkpoint_id": self.checkpoint.checkpoint_id}},
            values=self.checkpoint.values,
            next=("answer",),
            metadata={"source": "durable-checkpoint"},
        )


class RestartedWorkerGraph:
    def __init__(self, checkpoint):
        self.checkpointer = object()
        self.checkpoint = checkpoint
        self.calls = []
        self.configs = []

    async def astream(self, payload, *, config, stream_mode):
        self.calls.append((payload, stream_mode))
        self.configs.append(config)
        if payload is not None:
            raise AssertionError("recovery must resume from checkpoint, not replay input")
        self.checkpoint.checkpoint_id = "checkpoint-complete"
        self.checkpoint.values = {
            "messages": [
                {"role": "user", "content": "calculate 1 + 1", "id": "user-message"},
                {"role": "assistant", "content": "working", "id": "partial-message"},
                {
                    "role": "assistant",
                    "content": "2",
                    "id": "final-message",
                    "usage_metadata": {
                        "input_tokens": 5,
                        "output_tokens": 2,
                        "total_tokens": 7,
                        "model": "deterministic-test-model",
                    },
                },
            ]
        }
        yield self.checkpoint.values

    async def aget_state(self, config):
        return SimpleNamespace(
            config={"configurable": {"checkpoint_id": self.checkpoint.checkpoint_id}},
            values=self.checkpoint.values,
            next=(),
            metadata={"source": "durable-checkpoint"},
        )


class A2501KillRestartAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_worker_loss_restart_resumes_checkpoint_and_preserves_token_accounting(self):
        repository = InMemorySessionRepository()
        await repository.create_session(ConversationSession(
            tenant_id="tenant-a",
            session_id="session-a",
            owner_id="user-a",
        ))
        store = InMemoryRunStateStore()
        lifecycle = RunLifecycleManager(store)
        await store.create("tenant-a", "run-a")

        checkpoint = SharedCheckpoint()
        reached_checkpoint = asyncio.Event()
        hold_worker = asyncio.Event()
        first_graph = CrashingWorkerGraph(checkpoint, reached_checkpoint, hold_worker)
        first_executor = LangGraphRunExecutor(
            graph=first_graph,
            lifecycle=lifecycle,
            timeout_seconds=30,
            session_repository=repository,
            worker_id="worker-before-restart",
        )
        session_context = {"session_id": "session-a", "actor_id": "user-a"}
        original_input = {"prompt": "calculate 1 + 1"}

        worker_task = asyncio.create_task(first_executor.execute(
            tenant_id="tenant-a",
            run_id="run-a",
            input=original_input,
            session_context=session_context,
        ))
        await asyncio.wait_for(reached_checkpoint.wait(), timeout=2)

        # Simulate the worker being killed after LangGraph persisted a checkpoint.
        worker_task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await worker_task

        interrupted = await store.get("tenant-a", "run-a")
        self.assertEqual(interrupted.status, RunStatus.INTERRUPTED)
        self.assertEqual(
            interrupted.checkpoint_ref,
            "checkpoint-after-partial-response",
        )

        # A distinct executor/graph instance reopens the same checkpoint store.
        restarted_graph = RestartedWorkerGraph(checkpoint)
        restarted_executor = LangGraphRunExecutor(
            graph=restarted_graph,
            lifecycle=lifecycle,
            timeout_seconds=30,
            session_repository=repository,
            worker_id="worker-after-restart",
        )
        await restarted_executor.resume_claimed(
            snapshot=interrupted,
            input=original_input,
            session_context=session_context,
        )

        self.assertEqual(restarted_graph.calls, [(None, "values")])
        self.assertEqual(
            restarted_graph.configs[0]["configurable"]["platform_session_history"][0]["content"],
            "calculate 1 + 1",
        )
        self.assertEqual((await store.get("tenant-a", "run-a")).status, RunStatus.SUCCEEDED)

        messages = await repository.list_messages("tenant-a", "session-a")
        self.assertEqual(
            [(item.role.value, item.content) for item in messages],
            [
                ("user", "calculate 1 + 1"),
                ("assistant", "working"),
                ("assistant", "2"),
            ],
        )
        user_messages = [item for item in messages if item.role.value == "user"]
        self.assertEqual(len(user_messages), 1)
        final_messages = [item for item in messages if item.content == "2"]
        self.assertEqual(len(final_messages), 1)
        self.assertIsNotNone(final_messages[0].token_usage)
        self.assertEqual(final_messages[0].token_usage.prompt_tokens, 5)
        self.assertEqual(final_messages[0].token_usage.completion_tokens, 2)
        self.assertEqual(final_messages[0].token_usage.total_tokens, 7)
        self.assertEqual(
            sum(item.token_usage.total_tokens for item in messages if item.token_usage),
            7,
        )

        snapshots = await repository.list_context_snapshots("tenant-a", "session-a")
        self.assertTrue(snapshots)
        self.assertEqual(snapshots[0].snapshot["checkpoint_id"], "checkpoint-complete")


if __name__ == "__main__":
    unittest.main()
