from __future__ import annotations

import asyncio
import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.lifecycle import (
    InMemoryRunStateStore,
    RunLifecycleManager,
    RunStatus,
)
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult
from agent_platform.runtime.structured_output import (
    SchemaValidatedHarnessAdapter,
    StructuredOutputValidationError,
)


class _OutputAdapter:
    def __init__(self, outputs: list[str]) -> None:
        self.outputs = list(outputs)
        self.calls = 0

    async def run(self, request: HarnessRequest) -> HarnessResult:
        self.calls += 1
        return HarnessResult(output_text=self.outputs.pop(0))


class RuntimeAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def _running_run(self, run_id: str = "run-acceptance"):
        store = InMemoryRunStateStore()
        await store.create("tenant-a", run_id)
        manager = RunLifecycleManager(store)
        await manager.start("tenant-a", run_id)
        return store, manager, run_id

    async def test_explicit_cancellation_reaches_terminal_state(self):
        store, manager, run_id = await self._running_run()
        entered = asyncio.Event()

        async def operation():
            entered.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            manager.run_with_timeout("tenant-a", run_id, operation(), timeout=30)
        )
        await entered.wait()
        cancelling = await manager.cancel("tenant-a", run_id)
        self.assertEqual(cancelling.status, RunStatus.CANCELLING)
        with self.assertRaises(asyncio.CancelledError):
            await task

        snapshot = await store.get("tenant-a", run_id)
        self.assertEqual(snapshot.status, RunStatus.CANCELLED)
        self.assertIn(snapshot.status, {
            RunStatus.SUCCEEDED, RunStatus.FAILED,
            RunStatus.CANCELLED, RunStatus.TIMED_OUT,
        })

    async def test_timeout_cancels_operation_and_publishes_timed_out_state(self):
        store, manager, run_id = await self._running_run("run-timeout")
        cancelled = asyncio.Event()

        async def operation():
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.set()

        with self.assertRaises(asyncio.TimeoutError):
            await manager.run_with_timeout(
                "tenant-a", run_id, operation(), timeout=0.01,
            )

        snapshot = await store.get("tenant-a", run_id)
        self.assertTrue(cancelled.is_set())
        self.assertEqual(snapshot.status, RunStatus.TIMED_OUT)
        self.assertEqual(snapshot.last_error, "Run execution timed out")

    async def test_interrupted_run_recovers_from_durable_checkpoint_reference(self):
        store, manager, run_id = await self._running_run("run-recovery")
        await manager.mark_interrupted(
            "tenant-a", run_id,
            checkpoint_ref="checkpoint://tenant-a/run-recovery/step-4",
            reason="worker process stopped",
        )
        loaded = []
        resumed = []

        async def load_checkpoint(snapshot):
            loaded.append(snapshot.checkpoint_ref)

        async def resume(snapshot):
            resumed.append(snapshot.run_id)

        recovered = await manager.recover(
            checkpoint_loader=load_checkpoint, resume=resume,
        )

        self.assertEqual(recovered, [run_id])
        self.assertEqual(loaded, ["checkpoint://tenant-a/run-recovery/step-4"])
        self.assertEqual(resumed, [run_id])
        snapshot = await store.get("tenant-a", run_id)
        self.assertEqual(snapshot.status, RunStatus.RUNNING)
        self.assertEqual(
            snapshot.checkpoint_ref,
            "checkpoint://tenant-a/run-recovery/step-4",
        )

    async def test_structured_output_exhaustion_is_bounded_and_explicit(self):
        request = HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id="run-structured",
                agent_id="agent-a",
                model_template=ModelTemplateRef("general", 1),
            ),
            instructions="Return a JSON decision.",
            input_text="Decide.",
            output_schema={
                "type": "object",
                "required": ["decision"],
                "additionalProperties": False,
                "properties": {"decision": {"type": "string"}},
            },
        )
        adapter = _OutputAdapter(["not-json", '{"unexpected":true}'])
        validated = SchemaValidatedHarnessAdapter(adapter, max_retries=1)

        with self.assertRaises(StructuredOutputValidationError) as raised:
            await validated.run(request)

        self.assertEqual(adapter.calls, 2)
        self.assertEqual(raised.exception.attempts, 2)
        self.assertTrue(raised.exception.errors)
        self.assertIn("required property", " ".join(raised.exception.errors))


if __name__ == "__main__":
    unittest.main()
