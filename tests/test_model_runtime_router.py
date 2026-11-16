from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from agent_platform.models.registry_resolver import RegistryModelResolver
from agent_platform.models.runtime_router import (
    ModelRoutingError,
    ModelTransportError,
    ModelUsageRecordingError,
    OpenAICompatibleHTTPTransport,
    OpenAICompatibleModelRouter,
    PostgresModelUsageRecorder,
)
from agent_platform.observability.metrics import InMemoryMetricsRecorder
from agent_platform.quotas.service import Budget, InMemoryQuotaStore, QuotaService


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



    async def test_model_attempts_emit_nested_traces_and_low_cardinality_metrics(self):
        class Span:
            def __init__(self):
                self.updates = []
            def update(self, **kwargs):
                self.updates.append(kwargs)

        class Observer:
            def __init__(self):
                self.calls = []
                self.spans = []
            @contextmanager
            def observe(self, *, name, input, metadata):
                span = Span()
                self.calls.append((name, input, metadata))
                self.spans.append(span)
                yield span

        observer = Observer()
        metrics = InMemoryMetricsRecorder()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=FallbackTransport(),
            observer=observer,
            metrics=metrics,
        )
        result = await router.complete(
            snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-traced",
            template_id="primary", fallback_template_ids=("fallback",),
            messages=({"role": "user", "content": "do not capture this prompt"},),
            request_id="request-traced", required_capabilities=("text",),
        )
        self.assertTrue(result.fallback_used)
        self.assertEqual(len(observer.calls), 2)
        self.assertEqual([call[0] for call in observer.calls], ["model.complete", "model.complete"])
        self.assertEqual(observer.calls[0][2]["run_id"], "run-traced")
        self.assertNotIn("do not capture", str(observer.calls[0][1]))
        self.assertEqual(observer.spans[0].updates[0]["output"]["status"], "failed")
        self.assertEqual(observer.spans[1].updates[0]["output"]["status"], "response_received")
        counters = metrics.snapshot()["counters"]
        self.assertIn('model_requests_total{component="model",model_template="primary@1",status="failed"}', counters)
        self.assertIn('model_requests_total{component="model",model_template="fallback@2",status="response_received"}', counters)
        self.assertIn("model_fallbacks_total", " ".join(counters))
        self.assertIn("model_input_tokens_total", " ".join(counters))
        self.assertIn("model_output_tokens_total", " ".join(counters))


    async def test_missing_provider_usage_is_not_treated_as_zero_or_fallback(self):
        class MissingUsageTransport:
            def __init__(self):
                self.calls = 0
            async def complete(self, *, config, messages, request_parameters, timeout_seconds):
                self.calls += 1
                return {"choices": [{"message": {"content": "billable response"}}]}

        transport = MissingUsageTransport()
        usage = UsageRecorder()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=transport, usage_recorder=usage,
        )
        with self.assertRaisesRegex(ModelUsageRecordingError, "without provider usage"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-no-usage",
                template_id="primary", fallback_template_ids=("fallback",),
                messages=({"role": "user", "content": "hello"},),
                request_id="request-no-usage",
            )
        self.assertEqual(transport.calls, 1)
        self.assertEqual(usage.records, [])

    async def test_boolean_token_usage_is_rejected(self):
        class BooleanUsageTransport:
            async def complete(self, *, config, messages, request_parameters, timeout_seconds):
                return {
                    "choices": [{"message": {"content": "answer"}}],
                    "usage": {"prompt_tokens": True, "completion_tokens": 2},
                }

        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=BooleanUsageTransport(),
        )
        with self.assertRaisesRegex(ModelUsageRecordingError, "invalid token usage counters"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-bool-usage",
                template_id="primary",
                messages=({"role": "user", "content": "hello"},),
                request_id="request-bool-usage",
            )


    async def test_model_router_reserves_and_settles_provider_usage(self):
        class TokenEstimator:
            def estimate(self, **kwargs):
                return SimpleNamespace(input_tokens=4, output_tokens=8, total_tokens=12)

        class CostEstimator:
            async def estimate_micros(self, *, model_id, usage):
                return 1000

            def price_card_version(self, model_id):
                return "test-pricing-v1"

        class SuccessTransport:
            async def complete(self, **kwargs):
                return {
                    "choices": [{"message": {"content": "quota answer"}}],
                    "usage": {"prompt_tokens": 3, "completion_tokens": 2},
                }

        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        quotas = QuotaService(store)
        await quotas.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=100, hard_input_tokens=50, hard_output_tokens=50,
            hard_cost_usd=Decimal("0.01"), hard_concurrency=1,
        ))
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()), transport=SuccessTransport(),
            quota_service=quotas, token_estimator=TokenEstimator(),
            quota_cost_estimator=CostEstimator(),
        )
        result = await router.complete(
            snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-quota",
            template_id="primary", messages=({"role": "user", "content": "hello"},),
            request_id="request-quota", request_parameters={"max_tokens": 8},
        )
        self.assertEqual(result.content, "quota answer")
        tokens, cost, concurrency = await store.usage(
            "tenant-a", "tenant", "tenant-a",
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        self.assertEqual((tokens, cost, concurrency), (5, Decimal("0.001"), 0))
        self.assertEqual(
            next(iter(store._events.values())).price_card_version, "test-pricing-v1",
        )

    async def test_ambiguous_provider_failure_retains_quota_and_disables_fallback(self):
        class TokenEstimator:
            def estimate(self, **kwargs):
                return SimpleNamespace(input_tokens=4, output_tokens=8, total_tokens=12)

        class CostEstimator:
            async def estimate_micros(self, *, model_id, usage):
                return 1000

        class AmbiguousTransport:
            def __init__(self):
                self.calls = []

            async def complete(self, *, config, **kwargs):
                self.calls.append((config.provider_id, config.model_id))
                raise ModelTransportError("connection lost after request write")

        now = datetime.now(timezone.utc)
        store = InMemoryQuotaStore()
        quotas = QuotaService(store)
        await quotas.configure(Budget(
            tenant_id="tenant-a", scope_type="tenant", scope_id="tenant-a",
            period_start=now - timedelta(hours=1), period_end=now + timedelta(hours=1),
            hard_tokens=100, hard_input_tokens=50, hard_output_tokens=50,
            hard_cost_usd=Decimal("0.01"), hard_concurrency=2,
        ))
        transport = AmbiguousTransport()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()), transport=transport,
            quota_service=quotas, token_estimator=TokenEstimator(),
            quota_cost_estimator=CostEstimator(),
        )
        with self.assertRaisesRegex(ModelUsageRecordingError, "outcome is ambiguous"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-ambiguous",
                template_id="primary", fallback_template_ids=("fallback",),
                messages=({"role": "user", "content": "hello"},),
                request_id="request-ambiguous", request_parameters={"max_tokens": 8},
            )
        self.assertEqual(transport.calls, [("primary", "model-a")])
        tokens, _cost, concurrency = await store.usage(
            "tenant-a", "tenant", "tenant-a",
            now - timedelta(hours=1), now + timedelta(hours=1),
        )
        self.assertEqual((tokens, concurrency), (12, 1))

    async def test_context_estimation_is_enforced_without_configured_quota_budget(self):
        class RejectingEstimator:
            def __init__(self):
                self.calls = 0
                self.context_window = None

            def estimate(self, **kwargs):
                self.calls += 1
                self.context_window = kwargs["context_window_tokens"]
                raise ValueError("estimated prompt plus planned output exceeds model context window")

        class NeverDispatchTransport:
            def __init__(self):
                self.calls = 0

            async def complete(self, **kwargs):
                self.calls += 1
                return {
                    "choices": [{"message": {"content": "must not dispatch"}}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1},
                }

        estimator = RejectingEstimator()
        transport = NeverDispatchTransport()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=transport,
            token_estimator=estimator,
        )
        with self.assertRaisesRegex(ValueError, "exceeds model context window"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a",
                run_id="run-context-guard", template_id="primary",
                messages=({"role": "user", "content": "too much context"},),
                request_id="request-context-guard",
                request_parameters={"max_tokens": 8},
            )
        self.assertEqual(estimator.calls, 1)
        self.assertGreater(estimator.context_window, 0)
        self.assertEqual(transport.calls, 0)


    async def test_token_estimator_receives_pinned_model_context_window(self):
        class RecordingEstimator:
            def __init__(self):
                self.arguments = None

            def estimate(self, **kwargs):
                self.arguments = kwargs
                return SimpleNamespace(input_tokens=12, output_tokens=10, total_tokens=22)

        class SuccessTransport:
            def __init__(self):
                self.calls = []

            async def complete(self, *, config, messages, request_parameters, timeout_seconds):
                self.calls.append((config.provider_id, config.model_id))
                return {
                    "choices": [{"message": {"content": "budget answer"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 10},
                }

        estimator = RecordingEstimator()
        transport = SuccessTransport()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=transport,
            token_estimator=estimator,
        )
        await router.complete(
            snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-budget",
            template_id="primary",
            messages=({"role": "user", "content": "hello"},),
            request_id="request-budget",
            request_parameters={"max_tokens": 10},
        )
        self.assertEqual(estimator.arguments["model_id"], "model-a")
        self.assertEqual(estimator.arguments["context_window_tokens"], 8192)
        self.assertEqual(transport.calls, [("primary", "model-a")])
    async def test_token_estimation_failure_blocks_provider_dispatch(self):
        from agent_platform.quotas.token_estimator import TokenEstimationError

        class RejectingEstimator:
            def estimate(self, **_kwargs):
                raise TokenEstimationError("no tokenizer mapping is available")

        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=transport,
            token_estimator=RejectingEstimator(),
        )
        with self.assertRaisesRegex(TokenEstimationError, "no tokenizer mapping"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-unknown-tokenizer",
                template_id="primary",
                messages=({"role": "user", "content": "hello"},),
                request_id="request-unknown-tokenizer",
                request_parameters={"max_tokens": 10},
            )
        self.assertEqual(transport.calls, [])


if __name__ == "__main__":
    unittest.main()
