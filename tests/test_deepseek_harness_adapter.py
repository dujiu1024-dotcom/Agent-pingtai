import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.models.templates import ModelTemplateRegistry
from agent_platform.runtime.deepseek_harness import DeepSeekHarnessAdapter
from agent_platform.runtime.protocols import HarnessRequest


class FakeHarness:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.runs = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, prompt, *, session_id):
        self.runs.append((prompt, session_id))
        return SimpleNamespace(
            final_response="analysis complete",
            finish_reason="completed",
            events=[{"type": "assistant/message"}],
            notifications=[],
        )


class DeepSeekHarnessAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.home = root / "dsh-home"
        self.fake = FakeHarness
        self.instances = []

        def factory(**kwargs):
            instance = FakeHarness(**kwargs)
            self.instances.append(instance)
            return instance

        self.factory = factory
        self.registry = ModelTemplateRegistry.from_file(
            self._template_file(root)
        )
        self.context = RunContext(
            tenant_id="tenant-a",
            run_id="run-001",
            agent_id="expert-a",
            model_template=ModelTemplateRef("deepseek-general", 1),
        )
        self.request = HarnessRequest(
            context=self.context,
            instructions="Be precise.",
            input_text="Analyze the issue.",
        )

    def _template_file(self, root):
        path = root / "templates.json"
        path.write_text(
            """{
              "version": 1,
              "templates": [{
                "id": "deepseek-general",
                "version": 1,
                "provider": "deepseek-official",
                "model": "deepseek-v4-flash",
                "parameters": {"max_tokens": 1024, "timeout_seconds": 20},
                "reasoning": {"effort": "medium"},
                "capabilities": ["text"],
                "allowed_data_levels": ["public"],
                "status": "active"
              }]
            }""",
            encoding="utf-8",
        )
        return path

    def adapter(self, **overrides):
        options = {
            "templates": self.registry,
            "workspace_resolver": lambda _ctx: self.workspace,
            "home_resolver": lambda _ctx: self.home,
            "credential_resolver": lambda _template, _ctx: {
                "DEEPSEEK_API_KEY": "scoped-proxy-token",
                "DEEPSEEK_BASE_URL": "http://model-proxy.internal/v1"
            },
            "container_isolation_verified": True,
            "sdk_factory": self.factory,
        }
        options.update(overrides)
        return DeepSeekHarnessAdapter(**options)

    async def test_runs_with_explicit_workspace_home_and_permission_mode(self):
        result = await self.adapter().run(self.request)
        self.assertEqual(result.output_text, "analysis complete")
        self.assertEqual(result.finish_reason, "completed")
        self.assertEqual(result.metadata["profile"], "sdk-minimal")
        options = self.instances[0].options
        self.assertEqual(options["cwd"], str(self.workspace.resolve()))
        self.assertEqual(options["dsh_home"], str(self.home.resolve()))
        self.assertEqual(options["env"]["DSH_PERMISSION_MODE"], "read-only")

    async def test_requires_verified_sandbox(self):
        with self.assertRaisesRegex(RuntimeError, "verified container filesystem and network isolation"):
            await self.adapter(container_isolation_verified=False).run(self.request)

    async def test_rejects_tool_calls_until_gateway_profile_is_ready(self):
        request = HarnessRequest(
            context=self.context,
            instructions="",
            input_text="",
            allowed_tools=("send_message",),
        )
        with self.assertRaisesRegex(RuntimeError, "gateway-only profile"):
            await self.adapter().run(request)

    async def test_requires_custom_profile_for_non_deepseek_provider(self):
        path = Path(self.temp.name) / "other.json"
        path.write_text(
            """{
              "version": 1,
              "templates": [{
                "id": "other",
                "version": 1,
                "provider": "openai-compatible",
                "model": "model-a",
                "parameters": {},
                "status": "active"
              }]
            }""",
            encoding="utf-8",
        )
        registry = ModelTemplateRegistry.from_file(path)
        context = RunContext(
            tenant_id="tenant-a",
            run_id="run-002",
            agent_id="expert-b",
            model_template=ModelTemplateRef("other", 1),
        )
        request = HarnessRequest(
            context=context, instructions="", input_text=""
        )
        adapter = self.adapter(templates=registry)
        with self.assertRaisesRegex(RuntimeError, "bundled DeepSeek provider"):
            await adapter.run(request)


if __name__ == "__main__":
    unittest.main()
