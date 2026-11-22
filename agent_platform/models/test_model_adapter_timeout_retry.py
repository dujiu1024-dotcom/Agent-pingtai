from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.models.registry_resolver import RegistryModelResolver
from agent_platform.models.runtime_router import ModelRoutingError, ModelTransportError, OpenAICompatibleModelRouter


class _Secrets:
    async def resolve(self, _secret_ref: str) -> str:
        return "test-secret"


class _Audit:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def record(self, **event) -> None:
        self.events.append(event)


class _TimeoutTransport:
    def __init__(self, *, fail_all: bool = False) -> None:
        self.calls: list[tuple[str, float]] = []
        self.fail_all = fail_all

    async def complete(self, *, config, messages, request_parameters, timeout_seconds,
                       tools=None, tool_choice=None, on_token=None):
        self.calls.append((config.provider_id, timeout_seconds))
        if config.provider_id == "primary" or self.fail_all:
            raise ModelTransportError("model provider request timed out")
        return {
            "choices": [{"message": {"content": "fallback response"}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        }


class ModelAdapterTimeoutRetryTests(unittest.IsolatedAsyncioTestCase):
    def snapshot(self):
        return SimpleNamespace(
            tenant_id="tenant-a",
            entries=(
                {
                    "kind": "model_template", "item_id": "primary", "version": 1,
                    "definition": {
                        "provider_id": "primary", "provider_version": 1,
                        "model_id": "model-a", "parameters": {},
                        "capabilities": ["text"], "allowed_data_levels": ["internal"],
                    },
                },
                {
                    "kind": "model_template", "item_id": "fallback", "version": 1,
                    "definition": {
                        "provider_id": "fallback", "provider_version": 1,
                        "model_id": "model-b", "parameters": {},
                        "capabilities": ["text"], "allowed_data_levels": ["internal"],
                    },
                },
                {
                    "kind": "model_provider", "item_id": "primary", "version": 1,
                    "definition": {
                        "provider_type": "openai-compatible",
                        "base_url": "https://primary.example/v1",
                        "secret_ref": "secret://tenant-a/primary",
                    },
                },
                {
                    "kind": "model_provider", "item_id": "fallback", "version": 1,
                    "definition": {
                        "provider_type": "openai-compatible",
                        "base_url": "https://fallback.example/v1",
                        "secret_ref": "secret://tenant-a/fallback",
                    },
                },
            ),
        )

    async def test_timeout_transport_error_uses_only_explicit_fallback(self):
        timeout_seconds = 0.25
        transport = _TimeoutTransport()
        audit = _Audit()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(_Secrets()), transport=transport,
            audit_sink=audit, timeout_seconds=timeout_seconds,
        )
        result = await router.complete(
            snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-timeout-fallback",
            template_id="primary", fallback_template_ids=("fallback",),
            messages=({"role": "user", "content": "hello"},),
            request_id="request-timeout-fallback",
            required_capabilities=("text",), data_level="internal",
        )
        self.assertEqual(result.content, "fallback response")
        self.assertTrue(result.fallback_used)
        self.assertEqual(
            transport.calls, [("primary", timeout_seconds), ("fallback", timeout_seconds)],
        )
        failed = [
            event for event in audit.events
            if event["action"] == "model.response" and event["outcome"] == "failed"
        ]
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["detail"]["provider_id"], "primary")

    async def test_timeout_retries_are_bounded_by_explicit_candidate_list(self):
        transport = _TimeoutTransport(fail_all=True)
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(_Secrets()), transport=transport,
            audit_sink=_Audit(), timeout_seconds=0.1,
        )
        with self.assertRaisesRegex(ModelRoutingError, "all pinned model routes failed"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-timeout-exhausted",
                template_id="primary", fallback_template_ids=("fallback",),
                messages=({"role": "user", "content": "hello"},),
                request_id="request-timeout-exhausted",
                required_capabilities=("text",), data_level="internal",
            )
        self.assertEqual(
            [provider for provider, _timeout in transport.calls], ["primary", "fallback"],
        )
        self.assertEqual(len(transport.calls), 2)


if __name__ == "__main__":
    unittest.main()
