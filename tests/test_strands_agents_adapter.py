import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.models.templates import ModelTemplateRegistry
from agent_platform.runtime.protocols import HarnessRequest
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
        result = await self.adapter().run(self.request)
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
        result = await self.adapter(gateway_tool_provider=provider).run(request)
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
