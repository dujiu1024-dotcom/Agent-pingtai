from __future__ import annotations

import unittest

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.runtime.protocols import (
    CapabilityAwareHarnessAdapter,
    HarnessCapabilities,
    HarnessRequest,
    HarnessResult,
    MediaInput,
    MediaOutput,
    UnsupportedHarnessCapability,
)


DIGEST = "a" * 64


class FakeHarness:
    def __init__(self, result=None):
        self.result = result or HarnessResult(output_text="text result")
        self.calls = 0

    async def run(self, request):
        self.calls += 1
        return self.result


class HarnessMediaContractTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.context = RunContext(
            tenant_id="tenant-a",
            run_id="run-a",
            agent_id="agent-a",
            model_template=ModelTemplateRef("general", 1),
        )

    def test_media_references_require_authorized_resource_ids_and_matching_mime(self):
        image = MediaInput(
            modality="image",
            resource_id="asset-123",
            mime_type="image/png",
            size_bytes=1024,
            sha256=DIGEST,
            filename="evidence.png",
        )
        self.assertEqual(image.modality, "image")
        with self.assertRaisesRegex(ValueError, "opaque authorized resource"):
            MediaInput(
                modality="image", resource_id="https://example.com/image.png",
                mime_type="image/png", size_bytes=1024, sha256=DIGEST,
            )
        with self.assertRaisesRegex(ValueError, "does not match"):
            MediaInput(
                modality="pdf", resource_id="asset-123",
                mime_type="image/png", size_bytes=1024, sha256=DIGEST,
            )
        with self.assertRaisesRegex(ValueError, "sha256"):
            MediaOutput(
                modality="audio", resource_id="artifact-123",
                mime_type="audio/wav", size_bytes=512, sha256="invalid",
            )

    async def test_capability_guard_rejects_undeclared_media_before_model_dispatch(self):
        adapter = FakeHarness()
        guarded = CapabilityAwareHarnessAdapter(
            adapter=adapter,
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text"}),
            ),
        )
        request = HarnessRequest(
            context=self.context,
            instructions="Inspect evidence",
            input_text="",
            media_inputs=(
                MediaInput(
                    modality="image", resource_id="asset-123",
                    mime_type="image/png", size_bytes=1024, sha256=DIGEST,
                ),
            ),
        )
        with self.assertRaisesRegex(UnsupportedHarnessCapability, "image"):
            await guarded.run(request)
        self.assertEqual(adapter.calls, 0)

    async def test_capability_guard_accepts_declared_pdf_and_audio_inputs(self):
        adapter = FakeHarness()
        guarded = CapabilityAwareHarnessAdapter(
            adapter=adapter,
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text", "pdf", "audio"}),
                output_modalities=frozenset({"text"}),
            ),
        )
        request = HarnessRequest(
            context=self.context,
            instructions="Summarize the supplied sources",
            input_text="",
            media_inputs=(
                MediaInput(
                    modality="pdf", resource_id="asset-pdf",
                    mime_type="application/pdf", size_bytes=4096, sha256=DIGEST,
                ),
                MediaInput(
                    modality="audio", resource_id="asset-audio",
                    mime_type="audio/wav", size_bytes=2048, sha256=DIGEST,
                ),
            ),
        )
        result = await guarded.run(request)
        self.assertEqual(result.output_text, "text result")
        self.assertEqual(adapter.calls, 1)

    async def test_capability_guard_rejects_undeclared_media_outputs(self):
        adapter = FakeHarness(result=HarnessResult(
            output_text="",
            media_outputs=(
                MediaOutput(
                    modality="video", resource_id="artifact-video",
                    mime_type="video/mp4", size_bytes=2048, sha256=DIGEST,
                ),
            ),
        ))
        guarded = CapabilityAwareHarnessAdapter(
            adapter=adapter,
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text", "image"}),
            ),
        )
        request = HarnessRequest(
            context=self.context, instructions="", input_text="render a video"
        )
        with self.assertRaisesRegex(UnsupportedHarnessCapability, "video"):
            await guarded.run(request)

    async def test_harness_capabilities_reject_unknown_modality(self):
        with self.assertRaisesRegex(ValueError, "unsupported input modality"):
            HarnessCapabilities(input_modalities=frozenset({"text", "3d"}))


if __name__ == "__main__":
    unittest.main()
