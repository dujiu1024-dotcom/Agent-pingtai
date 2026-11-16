from __future__ import annotations

import asyncio
import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.harness_benchmark import HarnessBenchmarkCase, HarnessBenchmarkRunner
from agent_platform.runtime.protocols import HarnessRequest


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    def factory(self, runtime, case):
        return HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id=f"{case.case_id}-{runtime}",
                agent_id="benchmark", model_template=ModelTemplateRef("general", 1),
            ),
            instructions=case.instructions, input_text=case.input_text,
        )

    async def test_timeout_cancels_adapter_and_records_failed_observation(self):
        cancelled = asyncio.Event()

        class SlowAdapter:
            async def run(self, request):
                try:
                    await asyncio.sleep(1)
                finally:
                    cancelled.set()

        report = await HarnessBenchmarkRunner(
            adapters={"slow": SlowAdapter()}, request_factory=self.factory,
        ).run([HarnessBenchmarkCase(
            "deadline", "do work", "input", timeout_seconds=0.01,
        )])
        row = report.observations[0]
        self.assertFalse(row.passed)
        self.assertEqual(row.error_type, "TimeoutError")
        self.assertEqual(row.finish_reason, "error")
        self.assertTrue(cancelled.is_set())

    def test_invalid_timeout_is_rejected(self):
        with self.assertRaises(ValueError):
            HarnessBenchmarkCase("invalid", "do work", "input", timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
