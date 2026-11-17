from __future__ import annotations

import asyncio
import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.lifecycle import (
    InMemoryRunStateStore,
    RunLifecycleManager,
    RunSnapshot,
    RunStatus,
)
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult
from agent_platform.runtime.structured_output import (
    SchemaValidatedHarnessAdapter,
    StructuredOutputValidationError,
)


class _OutputHarness:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.requests = []

    async def run(self, request):
        self.requests.append(request)
        return HarnessResult(output_text=self.outputs.pop(0))


class RuntimeAcceptanceA1203Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = InMemoryRunStateStore()
        await self.store.create("tenant-a", "run-a")
        self.lifecycle = RunLifecycleManager(self.store)

    async def test_cancel_transitions_running_run_and_cancels_active_work(self):
        await self.lifecycle.start("tenant-a", "run-a")
        started = asyncio.Event()
        cancelled = asyncio.Event()

        async def work():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        task = asyncio.create_task(
            self.lifecycle.run_with_timeout(
                "tenant-a", "run-a", work(), timeout=30,
            )
        )
        await started.wait()
        cancelling = await self.lifecycle.cancel("tenant-a", "run-a")
        self.assertEqual(cancelling.status, RunStatus.CANCELLING)
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cancelled.is_set())

        final = await self.lifecycle.finish(
            "tenant-a", "run-a", RunStatus.CANCELLED,
        )
        self.assertEqual(final.status, RunStatus.CANCELLED)
        self.assertGreater(final.version, cancelling.version)

    async def test_timeout_is_a_durable_terminal_run_state(self):
        await self.lifecycle.start("tenant-a", "run-a")
        with self.assertRaises(asyncio.TimeoutError):
            await self.lifecycle.run_with_timeout(
                "tenant-a", "run-a", asyncio.sleep(10), timeout=0.01,
            )
        snapshot = await self.store.get("tenant-a", "run-a")
        self.assertEqual(snapshot.status, RunStatus.TIMED_OUT)
        self.assertEqual(snapshot.last_error, "Run execution timed out")
        self.assertGreater(snapshot.version, 1)

    async def test_interrupted_run_restores_checkpoint_and_reports_running_state(self):
        await self.lifecycle.start("tenant-a", "run-a")
        current = await self.store.get("tenant-a", "run-a")
        await self.store.transition(
            RunSnapshot(
                "tenant-a", "run-a", RunStatus.INTERRUPTED, current.version,
                checkpoint_ref="checkpoint://run-a/step-4",
                last_error="worker process interrupted",
            ),
            expected_version=current.version,
        )
        loaded = []
        resumed = []

        async def load_checkpoint(snapshot):
            loaded.append(snapshot.checkpoint_ref)

        async def resume(snapshot):
            resumed.append((snapshot.status, snapshot.checkpoint_ref))

        recovered = await self.lifecycle.recover(
            checkpoint_loader=load_checkpoint,
            resume=resume,
        )
        self.assertEqual(recovered, ["run-a"])
        self.assertEqual(loaded, ["checkpoint://run-a/step-4"])
        self.assertEqual(
            resumed,
            [(RunStatus.RUNNING, "checkpoint://run-a/step-4")],
        )
        snapshot = await self.store.get("tenant-a", "run-a")
        self.assertEqual(snapshot.status, RunStatus.RUNNING)

    async def test_recovery_without_checkpoint_fails_closed(self):
        await self.lifecycle.start("tenant-a", "run-a")
        await self.lifecycle.mark_interrupted(
            "tenant-a", "run-a", checkpoint_ref=None,
            reason="worker process interrupted",
        )
        resumed = []
        recovered = await self.lifecycle.recover(
            checkpoint_loader=lambda _snapshot: None,
            resume=lambda snapshot: resumed.append(snapshot.run_id),
        )
        self.assertEqual(recovered, [])
        self.assertEqual(resumed, [])
        snapshot = await self.store.get("tenant-a", "run-a")
        self.assertEqual(snapshot.status, RunStatus.FAILED)
        self.assertIn("no checkpoint", snapshot.last_error)

    def _request(self):
        return HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id="run-a", agent_id="agent-a",
                model_template=ModelTemplateRef("acceptance-model", 1),
            ),
            instructions="Return a JSON object with an answer.",
            input_text="Answer the question.",
            output_schema={
                "type": "object",
                "required": ["answer"],
                "additionalProperties": False,
                "properties": {"answer": {"type": "string", "minLength": 1}},
            },
        )

    async def test_structured_output_invalid_first_attempt_is_repaired(self):
        harness = _OutputHarness([
            "not-json",
            '{"answer":"repaired"}',
        ])
        result = await SchemaValidatedHarnessAdapter(
            harness, max_retries=1,
        ).run(self._request())
        self.assertTrue(result.metadata["structured_output_valid"])
        self.assertEqual(result.metadata["structured_output_attempts"], 2)
        self.assertEqual(len(harness.requests), 2)
        self.assertIn("Validation errors", harness.requests[1].instructions)

    async def test_structured_output_exhaustion_returns_typed_failure(self):
        harness = _OutputHarness(["not-json", '{"unexpected":true}'])
        with self.assertRaises(StructuredOutputValidationError) as raised:
            await SchemaValidatedHarnessAdapter(
                harness, max_retries=1,
            ).run(self._request())
        self.assertEqual(raised.exception.attempts, 2)
        self.assertEqual(raised.exception.schema_version, None)
        self.assertEqual(len(harness.requests), 2)


if __name__ == "__main__":
    unittest.main()
