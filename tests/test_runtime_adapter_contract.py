from __future__ import annotations

import asyncio
import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.protocols import (
    RUNTIME_ADAPTER_CONTRACT_VERSION,
    HarnessCapabilities,
    HarnessRequest,
    HarnessResult,
    HarnessStreamEvent,
    LifecycleManagedHarnessAdapter,
    MediaInput,
    MediaOutput,
    RuntimeAdapterCompatibilityError,
    RuntimeAdapterDescriptor,
    UnsupportedHarnessCapability,
    ensure_runtime_adapter_compatible,
)


def request(*, output_schema=None, allowed_tools=(), timeout_seconds=None, resume=False, checkpoint_ref=None):
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
        allowed_tools=allowed_tools,
        timeout_seconds=timeout_seconds,
        resume=resume,
        checkpoint_ref=checkpoint_ref,
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

    async def timed_out(self, **kwargs):
        self.events.append(("timed_out", kwargs))

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

    async def test_tool_timeout_and_resume_requests_require_declared_capabilities(self):
        adapter = FakeAdapter()
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter, descriptor=descriptor(), lifecycle=Lifecycle(),
        )
        for payload in (
            request(allowed_tools=("search",)),
            request(timeout_seconds=5),
            request(resume=True, checkpoint_ref="checkpoint-1"),
        ):
            with self.assertRaises(UnsupportedHarnessCapability):
                await managed.run(payload)
        self.assertEqual(adapter.calls, 0)

    def test_request_rejects_invalid_timeout_and_checkpoint_reference(self):
        with self.assertRaisesRegex(ValueError, "finite positive"):
            request(timeout_seconds=0)
        with self.assertRaisesRegex(ValueError, "requires resume=True"):
            request(checkpoint_ref="checkpoint-1")

    async def test_stream_without_completion_is_a_lifecycle_failure(self):
        class IncompleteStreamAdapter(FakeAdapter):
            async def stream(self, _request):
                yield HarnessStreamEvent(event_type="text_delta", text_delta="partial")

        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=IncompleteStreamAdapter(),
            descriptor=descriptor(capabilities=HarnessCapabilities(streaming=True)),
            lifecycle=lifecycle,
        )
        with self.assertRaisesRegex(RuntimeError, "without a completion event"):
            _ = [event async for event in managed.stream(request())]
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

    async def test_lifecycle_records_timeout_separately_from_failure(self):
        class TimedOutAdapter(FakeAdapter):
            async def run(self, _request):
                raise TimeoutError("adapter timed out")

        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=TimedOutAdapter(), descriptor=descriptor(), lifecycle=lifecycle,
        )
        with self.assertRaisesRegex(TimeoutError, "adapter timed out"):
            await managed.run(request())
        self.assertEqual([event[0] for event in lifecycle.events], ["started", "timed_out"])

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
            _ = [event async for event in managed.stream(request())]
        self.assertEqual([event[0] for event in lifecycle.events], ["started", "failed"])

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
        events = [event async for event in managed.stream(request())]
        self.assertEqual(events[-1].result.output_text, "streamed")
        self.assertEqual([event[0] for event in managed.lifecycle.events], ["started", "succeeded"])


    def test_media_capability_policy_rejects_invalid_limits_and_undeclared_modalities(self):
        with self.assertRaisesRegex(ValueError, "positive integer"):
            HarnessCapabilities(
                input_modalities=frozenset({"text", "image"}),
                input_media_max_bytes={"image": 0},
            )
        with self.assertRaisesRegex(ValueError, "undeclared modality"):
            HarnessCapabilities(input_media_max_bytes={"video": 1024})

    async def test_all_multimodal_inputs_are_gated_by_declared_modality_mime_and_size(self):
        media = (
            MediaInput("image", "img-1", "image/png", 1024, "a" * 64),
            MediaInput("pdf", "pdf-1", "application/pdf", 2048, "b" * 64),
            MediaInput("audio", "audio-1", "audio/mpeg", 4096, "c" * 64),
            MediaInput("video", "video-1", "video/mp4", 8192, "d" * 64),
        )
        adapter = FakeAdapter()
        capabilities = HarnessCapabilities(
            input_modalities=frozenset({"text", "image", "pdf", "audio", "video"}),
            input_media_max_bytes={"image": 2048, "pdf": 4096, "audio": 8192, "video": 16384},
        )
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter, descriptor=descriptor(capabilities=capabilities), lifecycle=Lifecycle(),
        )
        payload = request()
        payload = HarnessRequest(
            context=payload.context, instructions=payload.instructions, input_text="",
            media_inputs=media,
        )
        await managed.run(payload)
        self.assertEqual(adapter.calls, 1)

    async def test_unsupported_media_input_is_rejected_before_adapter_execution(self):
        adapter = FakeAdapter()
        capabilities = HarnessCapabilities(input_modalities=frozenset({"text", "image"}))
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter, descriptor=descriptor(capabilities=capabilities), lifecycle=Lifecycle(),
        )
        payload = request()
        payload = HarnessRequest(
            context=payload.context, instructions=payload.instructions, input_text="",
            media_inputs=(MediaInput("video", "video-1", "video/mp4", 10, "e" * 64),),
        )
        with self.assertRaisesRegex(UnsupportedHarnessCapability, "input modalities"):
            await managed.run(payload)
        self.assertEqual(adapter.calls, 0)

    async def test_media_input_rejects_over_limit_and_unlisted_mime(self):
        cases = (
            (MediaInput("image", "img-2", "image/png", 2049, "f" * 64),
             {"image": 2048}, "exceeds max size"),
            (MediaInput("image", "img-3", "image/tiff", 100, "1" * 64),
             {}, "MIME type"),
        )
        for media, policy, message in cases:
            adapter = FakeAdapter()
            capabilities = HarnessCapabilities(
                input_modalities=frozenset({"text", "image"}),
                input_media_max_bytes=policy,
            )
            managed = LifecycleManagedHarnessAdapter(
                adapter=adapter, descriptor=descriptor(capabilities=capabilities), lifecycle=Lifecycle(),
            )
            payload = request()
            payload = HarnessRequest(
                context=payload.context, instructions=payload.instructions, input_text="",
                media_inputs=(media,),
            )
            with self.assertRaisesRegex(UnsupportedHarnessCapability, message):
                await managed.run(payload)
            self.assertEqual(adapter.calls, 0)

    async def test_undeclared_media_output_is_rejected(self):
        class MediaOutputAdapter(FakeAdapter):
            async def run(self, _request):
                return HarnessResult(
                    "done", media_outputs=(MediaOutput(
                        "pdf", "out-1", "application/pdf", 100, "2" * 64,
                    ),),
                )

        managed = LifecycleManagedHarnessAdapter(
            adapter=MediaOutputAdapter(), descriptor=descriptor(), lifecycle=Lifecycle(),
        )
        with self.assertRaisesRegex(UnsupportedHarnessCapability, "output modalities"):
            await managed.run(request())

    async def test_media_output_size_limit_is_enforced(self):
        class MediaOutputAdapter(FakeAdapter):
            async def run(self, _request):
                return HarnessResult(
                    "done", media_outputs=(MediaOutput(
                        "image", "out-2", "image/png", 2049, "3" * 64,
                    ),),
                )

        capabilities = HarnessCapabilities(
            output_modalities=frozenset({"text", "image"}),
            output_media_max_bytes={"image": 2048},
        )
        managed = LifecycleManagedHarnessAdapter(
            adapter=MediaOutputAdapter(),
            descriptor=descriptor(capabilities=capabilities), lifecycle=Lifecycle(),
        )
        with self.assertRaisesRegex(UnsupportedHarnessCapability, "output image exceeds"):
            await managed.run(request())

    async def test_cancellation_requires_declared_capability(self):
        adapter = FakeAdapter()
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter,
            descriptor=descriptor(capabilities=HarnessCapabilities(cancellation=False)),
            lifecycle=Lifecycle(),
        )
        with self.assertRaises(UnsupportedHarnessCapability):
            await managed.cancel(run_id="run-1")

    async def test_declared_cancellation_delegates_to_adapter(self):
        adapter = FakeAdapter()
        managed = LifecycleManagedHarnessAdapter(
            adapter=adapter, descriptor=descriptor(), lifecycle=Lifecycle(),
        )
        await managed.cancel(run_id="run-1")
        self.assertEqual(adapter.cancelled, ["run-1"])


    async def test_declared_timeout_is_enforced_and_recorded_for_run(self):
        class BlockingAdapter(FakeAdapter):
            async def run(self, _request):
                await asyncio.Event().wait()

        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=BlockingAdapter(),
            descriptor=descriptor(capabilities=HarnessCapabilities(timeout=True)),
            lifecycle=lifecycle,
        )
        with self.assertRaises(TimeoutError):
            await managed.run(request(timeout_seconds=0.01))
        self.assertEqual([event[0] for event in lifecycle.events], ["started", "timed_out"])

    async def test_declared_timeout_is_enforced_and_recorded_for_stream(self):
        class BlockingStreamAdapter(FakeAdapter):
            async def stream(self, _request):
                yield HarnessStreamEvent(event_type="text_delta", text_delta="partial")
                await asyncio.Event().wait()

        lifecycle = Lifecycle()
        managed = LifecycleManagedHarnessAdapter(
            adapter=BlockingStreamAdapter(),
            descriptor=descriptor(capabilities=HarnessCapabilities(streaming=True, timeout=True)),
            lifecycle=lifecycle,
        )
        with self.assertRaises(TimeoutError):
            _ = [event async for event in managed.stream(request(timeout_seconds=0.01))]
        self.assertEqual([event[0] for event in lifecycle.events], ["started", "timed_out"])


    def test_media_reference_rejects_header_injection_and_unsafe_identifiers(self):
        # 构造调用必须在 assertRaises 上下文里，__post_init__ 会抛 ValueError
        with self.assertRaises(ValueError):
            MediaInput("image", "asset\nX-Injected: yes", "image/png", 1, "a" * 64)
        with self.assertRaises(ValueError):
            MediaInput("image", "asset-1", "image/png\r\nX-Injected: yes", 1, "a" * 64)
        with self.assertRaises(ValueError):
            MediaInput("image", "asset-1", "image/png", 1, "a" * 64, "photo.png\r\nX-Test: yes")
        with self.assertRaises(ValueError):
            MediaInput("image", "asset-1", "image/png", 1, "a" * 64, "..")
        with self.assertRaises(ValueError):
            MediaInput("image", "asset with space", "image/png", 1, "a" * 64)

    def test_media_reference_accepts_safe_unicode_filename_and_mime_parameters(self):
        item = MediaInput(
            "image", "asset_123", "image/png; charset=binary",
            1, "b" * 64, "图像-01.png",
        )
        self.assertEqual(item.filename, "图像-01.png")

if __name__ == "__main__":
    unittest.main()
