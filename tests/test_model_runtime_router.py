from __future__ import annotations

import unittest
from types import SimpleNamespace

from agent_platform.models.registry_resolver import RegistryModelResolver
from agent_platform.models.runtime_router import (
    ModelRoutingError,
    ModelTransportError,
    OpenAICompatibleHTTPTransport,
    PostgresModelUsageRecorder,
    OpenAICompatibleModelRouter,
)


class Secrets:
    async def resolve(self, secret_ref):
        return "secret-value"


class FallbackTransport:
    def __init__(self):
        self.calls = []

    async def complete(self, *, config, messages, request_parameters, timeout_seconds):
        self.calls.append((config.provider_id, config.model_id))
        if config.provider_id == "primary":
            raise ModelTransportError("offline")
        return {
            "choices": [{"message": {"content": "fallback answer"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2},
        }


class UsageRecorder:
    def __init__(self):
        self.records = []

    async def record(self, **kwargs):
        self.records.append(kwargs)


class ModelRuntimeRouterTests(unittest.IsolatedAsyncioTestCase):
    def snapshot(self):
        return SimpleNamespace(tenant_id="tenant-a", entries=(
            {
                "kind": "model_template", "item_id": "primary", "version": 1,
                "definition": {
                    "provider_id": "primary", "provider_version": 1,
                    "model_id": "model-a", "parameters": {},
                    "capabilities": ["text"], "allowed_data_levels": ["internal"],
                },
            },
            {
                "kind": "model_template", "item_id": "fallback", "version": 2,
                "definition": {
                    "provider_id": "fallback", "provider_version": 1,
                    "model_id": "model-b", "parameters": {"temperature": 0.1},
                    "capabilities": ["text"], "allowed_data_levels": ["internal"],
                },
            },
            {
                "kind": "model_provider", "item_id": "primary", "version": 1,
                "definition": {
                    "provider_type": "openai-compatible",
                    "base_url": "https://primary.example/v1",
                    "secret_ref": "secret://tenant/primary",
                },
            },
            {
                "kind": "model_provider", "item_id": "fallback", "version": 1,
                "definition": {
                    "provider_type": "openai-compatible",
                    "base_url": "https://fallback.example/v1",
                    "secret_ref": "secret://tenant/fallback",
                },
            },
        ))

    async def test_routes_to_explicit_snapshot_fallback_and_records_usage(self):
        transport = FallbackTransport()
        usage = UsageRecorder()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=transport, usage_recorder=usage,
        )
        result = await router.complete(
            snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-a",
            template_id="primary", fallback_template_ids=("fallback",),
            messages=({"role": "user", "content": "hello"},), request_id="request-a",
            required_capabilities=("text",), data_level="internal",
        )
        self.assertEqual(result.content, "fallback answer")
        self.assertTrue(result.fallback_used)
        self.assertEqual(transport.calls, [("primary", "model-a"), ("fallback", "model-b")])
        self.assertEqual(usage.records[0]["provider_id"], "fallback")
        self.assertEqual(usage.records[0]["run_id"], "run-a")

    async def test_data_level_mismatch_fails_before_provider_call(self):
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()), transport=transport,
        )
        with self.assertRaises(ModelRoutingError):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-a",
                template_id="primary", messages=({"role": "user", "content": "secret"},),
                request_id="request-a", data_level="restricted",
            )
        self.assertEqual(transport.calls, [])

    async def test_http_transport_rejects_route_control_overrides(self):
        config = await RegistryModelResolver(Secrets()).resolve(
            snapshot=self.snapshot(), template_id="primary",
        )
        with self.assertRaises(ModelTransportError):
            await OpenAICompatibleHTTPTransport().complete(
                config=config, messages=({"role": "user", "content": "hi"},),
                request_parameters={"model": "attacker-model"}, timeout_seconds=1,
            )


    async def test_postgres_usage_recorder_persists_idempotent_costed_usage(self):
        class Connection:
            def __init__(self):
                self.arguments = None
                self.statement = ""
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return None
            async def execute(self, statement, arguments):
                self.statement = statement
                self.arguments = arguments

        connection = Connection()
        async def connect():
            return connection
        class Cost:
            async def estimate_micros(self, *, model_id, usage):
                return 1250
        recorder = PostgresModelUsageRecorder(connect, cost_estimator=Cost())
        await recorder.record(
            tenant_id="tenant-a", run_id="run-a", request_id="request-a",
            template_id="general", template_version=2, provider_id="provider",
            provider_version=4, model_id="model-x",
            usage={"prompt_tokens": 4, "completion_tokens": 2},
        )
        self.assertIn("ON CONFLICT (tenant_id,run_id,request_id) DO NOTHING", connection.statement)
        self.assertEqual(connection.arguments[0:8], (
            "tenant-a", "run-a", "request-a", "general", 2,
            "provider", 4, "model-x",
        ))
        self.assertEqual(connection.arguments[-1], 1250)



if __name__ == "__main__":
    unittest.main()
