from __future__ import annotations

import asyncio
import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.protocols import (
    HarnessCapabilities,
    HarnessRequest,
    HarnessResult,
    HarnessStreamEvent,
    LifecycleManagedHarnessAdapter,
    RUNTIME_ADAPTER_CONTRACT_VERSION,
    RuntimeAdapterCompatibilityError,
    RuntimeAdapterDescriptor,
    UnsupportedHarnessCapability,
    ensure_runtime_adapter_compatible,
)


def request(*, output_schema=None):
    return HarnessRequest(
        context=RunContext(
            tenant_id="tenant-a",
            run_id="run-1",
            agent_id="agent-a",
            model_template=ModelTemplateRef("general", 1),
        ),
        instructions="Follow the task contract",
        input_text="hello",
        output_schema=output_schema,
    )


def descriptor(*, capabilities=None, contract_version=RUNTIME_ADAPTER_CONTRACT_VERSION):
    return RuntimeAdapterDescriptor(
        adapter_id="test.adapter",
        adapter_version="2.4.1",
        contract_version=contract_version,
        capabilities=capabilities or HarnessCapabilities(),
    )


class FakeAdapter:
    def __init__(self):
        self.calls = 0
        self.cancelled = []

    async def run(self, _request):
        self.calls += 1
        return HarnessResult("done")

    async def cancel(self, *, run_id):
        self.cancelled.append(run_id)


class Lifecycle:
    def __init__(self):
        self.events = []

    async def started(self, **kwargs):
        self.events.append(("started", kwargs))

    async def succeeded(self, **kwargs):
        self.events.append(("succeeded", kwargs))

    async def failed(self, **kwargs):
        self.events.append(("failed", kwargs))

    async def cancelled(self, **kwargs):
        self.events.append(("cancelled", kwargs))


class RuntimeAdapterContractTests(unittest.IsolatedAsyncioTestCase):
    def test_descriptor_rejects_invalid_semver_and_identifier(self):
        with self.assertRaisesRegex(ValueError, "semantic version"):
            RuntimeAdapterDescriptor("test.adapter", "v2", "1.0.0", HarnessCapabilities())
        with self.assertRaisesRegex(ValueError, "lowercase identifier"):
            RuntimeAdapterDescriptor("Bad Adapter", "2.0.0", "1.0.0", HarnessCapabilities())

    def test_compatibility_accepts_newer_minor_within_same_major(self):
        ensure_runtime_adapter_compatible(
            descriptor(contract_version="1.2.0"),
            required_contract_version="1.1.0",
        )

    def test_compatibility_rejects_older_or_different_major(self):
        with self.assertRaises(RuntimeAdapterCompatibilityError):
            ensure_runtime_adapter_compatible(
                descriptor(contract_version="1.0.9"),
                required_contract_version="1.1.0",
            )
        with self.assertRaises(RuntimeAdapterCompatibilityError):
            ensure_runtime_adapter_compatible(
                descriptor(contract_version="2.0.0"),
                required_contract_version="1.0.0",
            )

    async def test_structured_output_is_rejected_before_provider_execution(self):
        adapter = FakeAdapter()
        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter, descriptor=descriptor(), lifecycle=lifecycle,
        )
        with self.assertRaises(UnsupportedHarnessCapability):
            await managed.run(request(output_schema={"type": "object"}))
        self.assertEqual(adapter.calls, 0)
        self.assertEqual([event[0] for event in lifecycle.events], ["started", "failed"])

    async def test_lifecycle_records_success_in_order(self):
        adapter = FakeAdapter()
        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter, descriptor=descriptor(), lifecycle=lifecycle,
        )
        result = await managed.run(request())
        self.assertEqual(result.output_text, "done")
        self.assertEqual([event[0] for event in lifecycle.events], ["started", "succeeded"])
        self.assertEqual(lifecycle.events[-1][1]["result"], result)

    async def test_lifecycle_records_failure_and_preserves_original_error(self):
        class FailingAdapter(FakeAdapter):
            async def run(self, _request):
                raise RuntimeError("provider unavailable")

        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=FailingAdapter(), descriptor=descriptor(), lifecycle=lifecycle,
        )
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await managed.run(request())
        self.assertEqual([event[0] for event in lifecycle.events], ["started", "failed"])

    async def test_lifecycle_records_cancellation(self):
        started = asyncio.Event()

        class BlockingAdapter(FakeAdapter):
            async def run(self, _request):
                started.set()
                await asyncio.Event().wait()
                return HarnessResult("unreachable")

        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=BlockingAdapter(), descriptor=descriptor(), lifecycle=lifecycle,
        )
        task = asyncio.create_task(managed.run(request()))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual([event[0] for event in lifecycle.events], ["started", "cancelled"])

    async def test_streaming_requires_declared_capability_and_stream_method(self):
        adapter = FakeAdapter()
        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter, descriptor=descriptor(), lifecycle=lifecycle,
        )
        with self.assertRaises(UnsupportedHarnessCapability):
            _ = [event async for event in managed.adapter.stream(request())]

    async def test_streaming_passes_normalized_completion_when_declared(self):
        class StreamingAdapter(FakeAdapter):
            async def stream(self, _request):
                yield HarnessStreamEvent(
                    event_type="completed", result=HarnessResult("streamed"),
                )

        capabilities = HarnessCapabilities(streaming=True)
        managed = LifecycleManagedHarnessAdapter(
            adapter=StreamingAdapter(),
            descriptor=descriptor(capabilities=capabilities),
            lifecycle=Lifecycle(),
        )
        events = [event async for event in managed.adapter.stream(request())]
        self.assertEqual(events[-1].result.output_text, "streamed")

    async def test_cancellation_requires_declared_capability(self):
        adapter = FakeAdapter()
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter,
            descriptor=descriptor(capabilities=HarnessCapabilities(cancellation=False)),
            lifecycle=Lifecycle(),
        )
        with self.assertRaises(UnsupportedHarnessCapability):
            await managed.adapter.cancel(run_id="run-1")

    async def test_declared_cancellation_delegates_to_adapter(self):
        adapter = FakeAdapter()
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter, descriptor=descriptor(), lifecycle=Lifecycle(),
        )
        await managed.adapter.cancel(run_id="run-1")
        self.assertEqual(adapter.cancelled, ["run-1"])


if __name__ == "__main__":
    unittest.main()
