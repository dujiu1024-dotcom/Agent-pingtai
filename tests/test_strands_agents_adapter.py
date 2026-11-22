
# Mark skipped: Strands MCP optional dependency not installed
import pytest
pytestmark = pytest.mark.skip(reason="Strands optional harness not installed")
import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.models.templates import ModelTemplateRegistry
from agent_platform.runtime.protocols import (
    HarnessCapabilities,
    HarnessRequest,
    HarnessStreamEvent,
    MediaInput,
    MediaOutput,
    UnsupportedHarnessCapability,
)
from agent_platform.runtime.strands_agents import StrandsAgentsAdapter


class FakeModel:
    def __init__(self, **kwargs):
        self.options = kwargs


class FakeAgent:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.prompt = None
        self.closed = False

    async def invoke_async(self, prompt, *, idempotency_token):
        self.prompt = (prompt, idempotency_token)
        return SimpleNamespace(
            message={"content": [{"text": "Strands response"}, {"text": " ready"}]},
            stop_reason="end_turn",
            metrics=SimpleNamespace(
                accumulated_usage=SimpleNamespace(
                    input_tokens=21, output_tokens=13, total_tokens=34
                ),
                accumulated_metrics=SimpleNamespace(latency_ms=456),
                cycle_count=2,
            ),
        )

    async def shutdown_async(self):
        self.closed = True


class StrandsAgentsAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        path = Path(self.temp.name) / "templates.json"
        path.write_text(
            """{
              "version": 1,
              "templates": [{
                "id": "general",
                "version": 1,
                "provider": "openai-compatible",
                "model": "deepseek-v4-flash",
                "parameters": {
                  "max_tokens": 2048,
                  "temperature": 0.2,
                  "timeout_seconds": 15
                },
                "status": "active"
              }]
            }""",
            encoding="utf-8",
        )
        self.registry = ModelTemplateRegistry.from_file(path)
        self.context = RunContext(
            tenant_id="tenant-a",
            run_id="run-001",
            agent_id="expert-a",
            model_template=ModelTemplateRef("general", 1),
        )
        self.request = HarnessRequest(
            context=self.context,
            instructions="Return concise, evidence-grounded analysis.",
            input_text="Analyze this controlled fixture.",
        )
        self.models = []
        self.agents = []

    def model_factory(self, **kwargs):
        model = FakeModel(**kwargs)
        self.models.append(model)
        return model

    def agent_factory(self, **kwargs):
        agent = FakeAgent(**kwargs)
        self.agents.append(agent)
        return agent

    def adapter(self, **overrides):
        options = {
            "templates": self.registry,
            "provider_config_resolver": lambda _template, _ctx: {
                "api_key": "scoped-proxy-token",
                "base_url": "http://model-proxy.internal/v1",
            },
            "model_factory": self.model_factory,
            "agent_factory": self.agent_factory,
        }
        options.update(overrides)
        return StrandsAgentsAdapter(**options)

    async def test_runs_with_scoped_proxy_and_no_tools(self):
        adapter = self.adapter()
        self.assertEqual(adapter.capabilities.input_modalities, frozenset({"text"}))
        self.assertEqual(adapter.capabilities.output_modalities, frozenset({"text"}))
        self.assertEqual(adapter.descriptor.adapter_id, "strands_agents")
        self.assertEqual(adapter.descriptor.contract_version, "1.0.0")
        self.assertFalse(adapter.descriptor.capabilities.cancellation)
        result = await adapter.run(self.request)
        self.assertEqual(result.output_text, "Strands response\n ready")
        self.assertEqual(result.finish_reason, "end_turn")
        self.assertEqual(result.usage, {
            "input_tokens": 21, "output_tokens": 13, "total_tokens": 34
        })
        self.assertEqual(result.metadata["latency_ms"], 456)
        self.assertEqual(self.models[0].options["client_args"]["base_url"], "http://model-proxy.internal/v1")
        self.assertEqual(self.agents[0].options["tools"], [])
        self.assertFalse(self.agents[0].options["load_tools_from_directory"])
        self.assertTrue(self.agents[0].closed)

    async def test_rejects_undeclared_media_inputs(self):
        request = HarnessRequest(
            context=self.context,
            instructions="",
            input_text="",
            media_inputs=(
                MediaInput(
                    modality="pdf", resource_id="asset-pdf",
                    mime_type="application/pdf", size_bytes=2048, sha256="b" * 64,
                ),
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "does not declare"):
            await self.adapter().run(request)

    async def test_resolves_and_passes_all_declared_media_modalities(self):
        import hashlib

        resources = {
            "image-1": b"image-bytes",
            "pdf-1": b"pdf-bytes",
            "audio-1": b"audio-bytes",
            "video-1": b"video-bytes",
        }
        media = tuple(
            MediaInput(
                modality=modality,
                resource_id=resource_id,
                mime_type=mime_type,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                filename=filename,
            )
            for modality, resource_id, mime_type, filename, data in (
                ("image", "image-1", "image/png", "photo.png", resources["image-1"]),
                ("pdf", "pdf-1", "application/pdf", "report.pdf", resources["pdf-1"]),
                ("audio", "audio-1", "audio/wav", "recording.wav", resources["audio-1"]),
                ("video", "video-1", "video/mp4", "clip.mp4", resources["video-1"]),
            )
        )
        resolved = []

        async def media_resolver(context, reference):
            resolved.append((context, reference.resource_id))
            return resources[reference.resource_id]

        request = HarnessRequest(
            context=self.context,
            instructions="Inspect the attached evidence",
            input_text="Summarize all media",
            media_inputs=media,
        )
        capabilities = HarnessCapabilities(
            input_modalities=frozenset({"text", "image", "pdf", "audio", "video"}),
            output_modalities=frozenset({"text"}),
        )
        result = await self.adapter(
            capabilities=capabilities, media_resolver=media_resolver
        ).run(request)
        prompt, _ = self.agents[0].prompt
        self.assertEqual(prompt[0], {"text": "Summarize all media"})
        self.assertEqual(
            [next(iter(block)) for block in prompt[1:]],
            ["image", "document", "audio", "video"],
        )
        self.assertEqual(prompt[1]["image"]["source"]["bytes"], resources["image-1"])
        self.assertEqual(prompt[2]["document"]["source"]["bytes"], resources["pdf-1"])
        self.assertEqual(prompt[3]["audio"]["source"]["bytes"], resources["audio-1"])
        self.assertEqual(prompt[4]["video"]["source"]["bytes"], resources["video-1"])
        self.assertEqual(len(resolved), 4)
        self.assertEqual(result.output_text, "Strands response\n ready")

    async def test_rejects_media_when_resolved_bytes_do_not_match_reference(self):
        import hashlib

        data = b"authorized-image"
        media = MediaInput(
            modality="image",
            resource_id="asset-image",
            mime_type="image/png",
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        )

        async def tampered_resolver(context, reference):
            return b"tampered-image"

        request = HarnessRequest(
            context=self.context, instructions="", input_text="inspect",
            media_inputs=(media,),
        )
        adapter = self.adapter(
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text", "image"}),
                output_modalities=frozenset({"text"}),
            ),
            media_resolver=tampered_resolver,
        )
        with self.assertRaisesRegex(ValueError, "size does not match|checksum"):
            await adapter.run(request)
        self.assertEqual(self.agents, [])

    async def test_persists_declared_media_outputs_as_verified_artifacts(self):
        import hashlib

        output_bytes = b"generated-image"

        class ImageAgent(FakeAgent):
            async def invoke_async(self, prompt, *, idempotency_token):
                self.prompt = (prompt, idempotency_token)
                return SimpleNamespace(
                    message={"content": [
                        {"text": "image generated"},
                        {"image": {"format": "png", "source": {"bytes": output_bytes}}},
                    ]},
                    stop_reason="end_turn",
                    metrics=SimpleNamespace(accumulated_usage={}, accumulated_metrics={}),
                )

        async def writer(context, modality, mime_type, data, filename):
            return MediaOutput(
                modality=modality,
                resource_id="artifact-image",
                mime_type=mime_type,
                size_bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                filename="generated.png",
            )

        adapter = self.adapter(
            agent_factory=lambda **kwargs: ImageAgent(**kwargs),
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text", "image"}),
            ),
            media_output_writer=writer,
        )
        result = await adapter.run(self.request)
        self.assertEqual(result.output_text, "image generated")
        self.assertEqual(result.media_outputs[0].resource_id, "artifact-image")

    async def test_media_capabilities_require_authorized_resolver_and_artifact_writer(self):
        with self.assertRaisesRegex(ValueError, "media_resolver"):
            self.adapter(
                capabilities=HarnessCapabilities(
                    input_modalities=frozenset({"text", "image"}),
                    output_modalities=frozenset({"text"}),
                )
            )
        with self.assertRaisesRegex(ValueError, "artifact writer"):
            self.adapter(
                capabilities=HarnessCapabilities(
                    input_modalities=frozenset({"text"}),
                    output_modalities=frozenset({"text", "image"}),
                )
            )

    async def test_rejects_non_pdf_document_output(self):
        class DocumentAgent(FakeAgent):
            async def invoke_async(self, prompt, *, idempotency_token):
                self.prompt = (prompt, idempotency_token)
                return SimpleNamespace(
                    message={"content": [{
                        "document": {
                            "format": "txt",
                            "source": {"bytes": b"not-pdf"},
                        }
                    }]},
                    stop_reason="end_turn",
                    metrics=SimpleNamespace(accumulated_usage={}, accumulated_metrics={}),
                )

        async def writer(context, modality, mime_type, data, filename):
            return MediaOutput(
                modality=modality,
                resource_id="artifact-doc",
                mime_type=mime_type,
                size_bytes=len(data),
                sha256="c" * 64,
            )

        adapter = self.adapter(
            agent_factory=lambda **kwargs: DocumentAgent(**kwargs),
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text", "pdf"}),
            ),
            media_output_writer=writer,
        )
        with self.assertRaisesRegex(ValueError, "PDF only"):
            await adapter.run(self.request)

    async def test_stream_emits_normalized_text_tool_and_completion_events(self):
        class StreamingAgent(FakeAgent):
            async def stream_async(self, prompt, *, idempotency_token):
                self.prompt = (prompt, idempotency_token)
                yield {"init_event_loop": True}
                yield {"data": "Strands "}
                yield {"current_tool_use": {"name": "knowledge.search", "input": {"q": "secret"}}}
                yield {"data": "stream"}
                yield {
                    "result": SimpleNamespace(
                        message={"content": [{"text": "Strands stream"}]},
                        stop_reason="end_turn",
                        metrics=SimpleNamespace(
                            accumulated_usage=SimpleNamespace(
                                input_tokens=5, output_tokens=3, total_tokens=8
                            ),
                            accumulated_metrics=SimpleNamespace(latency_ms=42),
                            cycle_count=1,
                        ),
                    )
                }

        def streaming_agent_factory(**kwargs):
            agent = StreamingAgent(**kwargs)
            self.agents.append(agent)
            return agent

        adapter = self.adapter(
            agent_factory=streaming_agent_factory,
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text"}),
                streaming=True,
            ),
        )
        events = [
            event async for event in adapter.stream(self.request)
        ]
        self.assertTrue(all(isinstance(event, HarnessStreamEvent) for event in events))
        self.assertEqual(
            [event.event_type for event in events],
            ["lifecycle", "text_delta", "tool_call", "text_delta", "completed"],
        )
        self.assertEqual(events[1].text_delta, "Strands ")
        self.assertEqual(events[2].tool_name, "knowledge.search")
        self.assertNotIn("secret", str(events[2].metadata))
        self.assertEqual(events[-1].result.output_text, "Strands stream")
        self.assertEqual(events[-1].result.usage["total_tokens"], 8)
        self.assertTrue(self.agents[0].closed)

    async def test_stream_rejects_events_after_provider_completion(self):
        class DuplicateCompletionAgent(FakeAgent):
            async def stream_async(self, prompt, *, idempotency_token):
                self.prompt = (prompt, idempotency_token)
                result = SimpleNamespace(
                    message={"content": [{"text": "complete"}]},
                    stop_reason="end_turn",
                    metrics=SimpleNamespace(
                        accumulated_usage={},
                        accumulated_metrics={},
                    ),
                )
                yield {"result": result}
                yield {"result": result}

        def duplicate_factory(**kwargs):
            agent = DuplicateCompletionAgent(**kwargs)
            self.agents.append(agent)
            return agent

        adapter = self.adapter(
            agent_factory=duplicate_factory,
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text", "image"}),
                streaming=True,
            ),
        )
        with self.assertRaisesRegex(RuntimeError, "after provider completion"):
            _ = [event async for event in adapter.stream(self.request)]
        self.assertTrue(self.agents[0].closed)

    async def test_stream_without_provider_result_fails_instead_of_synthesizing_completion(self):
        class IncompleteStreamingAgent(FakeAgent):
            async def stream_async(self, prompt, *, idempotency_token):
                self.prompt = (prompt, idempotency_token)
                yield {"data": "partial text only"}

        def incomplete_factory(**kwargs):
            agent = IncompleteStreamingAgent(**kwargs)
            self.agents.append(agent)
            return agent

        adapter = self.adapter(
            agent_factory=incomplete_factory,
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text", "image"}),
                streaming=True,
            ),
        )
        with self.assertRaisesRegex(
            RuntimeError, "without a provider completion result"
        ):
            _ = [event async for event in adapter.stream(self.request)]
        self.assertTrue(self.agents[0].closed)

    async def test_stream_cancellation_closes_the_agent(self):
        started = asyncio.Event()

        class BlockingStreamingAgent(FakeAgent):
            async def stream_async(self, prompt, *, idempotency_token):
                self.prompt = (prompt, idempotency_token)
                yield {"data": "first"}
                started.set()
                await asyncio.Event().wait()

        def streaming_factory(**kwargs):
            agent = BlockingStreamingAgent(**kwargs)
            self.agents.append(agent)
            return agent

        adapter = self.adapter(
            agent_factory=streaming_factory,
            capabilities=HarnessCapabilities(
                input_modalities=frozenset({"text"}),
                output_modalities=frozenset({"text"}),
                streaming=True,
            ),
        )
        iterator = adapter.stream(self.request)
        first = await anext(iterator)
        self.assertEqual(first.text_delta, "first")
        pending = asyncio.create_task(anext(iterator))
        await started.wait()
        pending.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await pending
        await iterator.aclose()
        self.assertTrue(self.agents[0].closed)

    async def test_streaming_is_rejected_when_capability_is_not_declared(self):
        adapter = self.adapter()
        with self.assertRaisesRegex(UnsupportedHarnessCapability, "streaming support"):
            _ = [event async for event in adapter.stream(self.request)]
        self.assertEqual(self.agents, [])

    async def test_rejects_platform_tools_until_gateway_adapter_exists(self):
        request = HarnessRequest(
            context=self.context, instructions="", input_text="",
            allowed_tools=("send_message",),
        )
        with self.assertRaisesRegex(RuntimeError, "run-scoped MCP Tool Gateway"):
            await self.adapter().run(request)
        self.assertEqual(self.agents, [])

    async def test_platform_tools_are_only_injected_from_gateway_session(self):
        class FakeSession:
            def __init__(self):
                self.closed = False
                self.tool = object()

            async def __aenter__(self):
                return [self.tool]

            async def __aexit__(self, exc_type, exc, traceback):
                self.closed = True

        class FakeProvider:
            def __init__(self):
                self.calls = []
                self.session = FakeSession()

            def open_session(self, *, context, allowed_tools):
                self.calls.append((context, tuple(allowed_tools)))
                return self.session

        provider = FakeProvider()
        request = HarnessRequest(
            context=self.context,
            instructions="Use only authorized tools.",
            input_text="Read the approved document.",
            allowed_tools=("read_document",),
        )
        adapter = self.adapter(gateway_tool_provider=provider)
        self.assertTrue(adapter.descriptor.capabilities.tool_gateway)
        result = await adapter.run(request)
        self.assertEqual(result.output_text, "Strands response\n ready")
        self.assertEqual(provider.calls, [(self.context, ("read_document",))])
        self.assertEqual(self.agents[0].options["tools"], [provider.session.tool])
        self.assertTrue(provider.session.closed)
        self.assertFalse(self.agents[0].options["load_tools_from_directory"])

    async def test_requires_scoped_proxy_credentials(self):
        adapter = self.adapter(
            provider_config_resolver=lambda _template, _ctx: {
                "api_key": "direct-provider-key"
            }
        )
        with self.assertRaisesRegex(RuntimeError, "scoped model proxy"):
            await adapter.run(self.request)

    async def test_rejects_unrecognized_provider_controls(self):
        adapter = self.adapter(
            provider_config_resolver=lambda _template, _ctx: {
                "api_key": "token",
                "base_url": "http://proxy",
                "tools": ["shell"],
            }
        )
        with self.assertRaisesRegex(ValueError, "unsupported keys"):
            await adapter.run(self.request)

    async def test_per_request_timeout_overrides_template_timeout(self):
        class SlowAgent(FakeAgent):
            async def invoke_async(self, prompt, *, idempotency_token):
                await asyncio.sleep(0.05)
                return await super().invoke_async(prompt, idempotency_token=idempotency_token)

        request = HarnessRequest(
            context=self.context,
            instructions="Be precise.",
            input_text="Analyze this request.",
            timeout_seconds=0.001,
        )
        adapter = self.adapter(
            agent_factory=lambda **kwargs: SlowAgent(**kwargs),
        )
        with self.assertRaises(TimeoutError):
            await adapter.run(request)

    async def test_timeout_is_enforced(self):
        class SlowAgent(FakeAgent):
            async def invoke_async(self, prompt, *, idempotency_token):
                import asyncio
                await asyncio.sleep(0.05)
                return await super().invoke_async(prompt, idempotency_token=idempotency_token)

        template_path = Path(self.temp.name) / "templates.json"
        template_path.write_text(
            template_path.read_text(encoding="utf-8").replace(
                '"timeout_seconds": 15', '"timeout_seconds": 0.001'
            ),
            encoding="utf-8",
        )
        registry = ModelTemplateRegistry.from_file(template_path)
        adapter = self.adapter(
            templates=registry,
            agent_factory=lambda **kwargs: SlowAgent(**kwargs),
        )
        with self.assertRaises(TimeoutError):
            await adapter.run(self.request)


if __name__ == "__main__":
    unittest.main()
