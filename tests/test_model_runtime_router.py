from __future__ import annotations

import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

from agent_platform.models.circuit_breaker import ModelCircuitBreaker
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
from agent_platform.quotas.service import Budget, BudgetExceeded, InMemoryQuotaStore, QuotaService


class Secrets:
    async def resolve(self, secret_ref):
        return "secret-value"


class FallbackTransport:
    def __init__(self):
        self.calls = []

    async def complete(self, *, config, messages, request_parameters, timeout_seconds, tools=None, tool_choice=None, on_token=None):
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


class AuditRecorder:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events = []

    async def record(self, **event):
        if self.fail:
            raise RuntimeError("audit database unavailable")
        self.events.append(event)


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
            audit_sink=AuditRecorder(),
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

    async def test_missing_or_unavailable_audit_sink_blocks_provider_dispatch(self):
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()), transport=transport,
            audit_sink=AuditRecorder(fail=True),
        )
        with self.assertRaisesRegex(ModelUsageRecordingError, "write-ahead audit failed"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-a",
                template_id="primary", messages=({"role": "user", "content": "hello"},),
                request_id="request-a",
            )
        self.assertEqual(transport.calls, [])

    async def test_model_request_and_result_are_audited(self):
        audit = AuditRecorder()
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()), transport=transport,
            audit_sink=audit,
        )
        await router.complete(
            snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-a",
            template_id="primary", fallback_template_ids=("fallback",),
            messages=({"role": "user", "content": "hello"},), request_id="request-a",
        )
        self.assertEqual(
            [(event["action"], event["outcome"], event["detail"]["phase"] if "phase" in event["detail"] else "result")
             for event in audit.events],
            [
                ("model.request", "started", "started"),
                ("model.response", "failed", "result"),
                ("model.request", "started", "started"),
                ("model.response", "succeeded", "result"),
            ],
        )
        self.assertEqual(audit.events[-1]["detail"]["input_tokens"], 3)
        self.assertNotIn("hello", str(audit.events))

    async def test_preflight_rejection_is_audited_without_provider_dispatch(self):
        audit = AuditRecorder()
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit,
            resolver=RegistryModelResolver(Secrets()), transport=transport,
        )
        with self.assertRaisesRegex(ModelRoutingError, "required capabilities"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-a",
                template_id="primary", required_capabilities=("vision",),
                messages=({"role": "user", "content": "hello"},),
                request_id="request-preflight",
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(
            [(event["action"], event["outcome"]) for event in audit.events],
            [("model.request", "started"), ("model.response", "denied")],
        )
        self.assertNotIn("hello", str(audit.events))

    async def test_data_level_mismatch_fails_before_provider_call(self):
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=AuditRecorder(),
            resolver=RegistryModelResolver(Secrets()), transport=transport,
        )
        with self.assertRaises(ModelRoutingError):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-a",
                template_id="primary", messages=({"role": "user", "content": "secret"},),
                request_id="request-a", data_level="restricted",
            )
        self.assertEqual(transport.calls, [])


    async def test_open_model_circuit_blocks_provider_before_quota_or_dispatch(self):
        breaker = ModelCircuitBreaker(failure_threshold=1, reset_timeout_seconds=60)
        await breaker.record_failure("tenant-a:primary:model-a")
        transport = FallbackTransport()
        audit = AuditRecorder()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit,
            resolver=RegistryModelResolver(Secrets()),
            transport=transport,
            circuit_breaker=breaker,
        )
        with self.assertRaisesRegex(
            ModelRoutingError,
            r"all pinned model routes failed: CircuitOpenError: model provider circuit is open",
        ):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-breaker",
                template_id="primary", messages=({"role": "user", "content": "hello"},),
                request_id="request-breaker",
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(audit.events[-1]["outcome"], "denied")
        self.assertEqual(audit.events[-1]["detail"]["reason"], "model_circuit_open")

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
            audit_sink=AuditRecorder(),
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
        self.assertIn("model_tokens_total", " ".join(counters))
        self.assertIn("model_input_tokens_total", " ".join(counters))
        self.assertIn("model_output_tokens_total", " ".join(counters))


    async def test_missing_provider_usage_is_not_treated_as_zero_or_fallback(self):
        class MissingUsageTransport:
            def __init__(self):
                self.calls = 0
            async def complete(self, *, config, messages, request_parameters, timeout_seconds, tools=None, tool_choice=None, on_token=None):
                self.calls += 1
                return {"choices": [{"message": {"content": "billable response"}}]}

        transport = MissingUsageTransport()
        usage = UsageRecorder()
        router = OpenAICompatibleModelRouter(
            audit_sink=AuditRecorder(),
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
            async def complete(self, *, config, messages, request_parameters, timeout_seconds, tools=None, tool_choice=None, on_token=None):
                return {
                    "choices": [{"message": {"content": "answer"}}],
                    "usage": {"prompt_tokens": True, "completion_tokens": 2},
                }

        router = OpenAICompatibleModelRouter(
            audit_sink=AuditRecorder(),
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
            audit_sink=AuditRecorder(),
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
            audit_sink=AuditRecorder(),
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

    async def test_token_estimation_failure_is_audited_and_never_tries_provider_or_fallback(self):
        class FailingEstimator:
            def estimate(self, **kwargs):
                raise RuntimeError("tokenizer unavailable")

        class NeverDispatchTransport:
            def __init__(self):
                self.calls = []

            async def complete(self, *, config, **kwargs):
                self.calls.append(config.provider_id)
                raise AssertionError("Provider dispatch must not occur")

        audit = AuditRecorder()
        transport = NeverDispatchTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit, resolver=RegistryModelResolver(Secrets()),
            transport=transport, token_estimator=FailingEstimator(),
        )
        with self.assertRaisesRegex(RuntimeError, "tokenizer unavailable"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-estimate-fail",
                template_id="primary", fallback_template_ids=("fallback",),
                messages=({"role": "user", "content": "private prompt"},),
                request_id="request-estimate-fail",
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(
            [(event["action"], event["outcome"]) for event in audit.events],
            [("model.request", "started"), ("model.response", "failed")],
        )
        self.assertEqual(audit.events[-1]["detail"]["reason"], "token_estimation_failed")
        self.assertEqual(audit.events[-1]["detail"]["error_type"], "RuntimeError")
        self.assertNotIn("private prompt", str(audit.events))

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
        audit = AuditRecorder()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit,
            resolver=RegistryModelResolver(Secrets()),
            transport=transport,
            token_estimator=estimator,
        )
        # The router exposes a stable routing-layer error while preserving the
        # context-overflow message and recording the fail-closed preflight result.
        with self.assertRaisesRegex(ModelRoutingError, "exceeds model context window"):
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
        self.assertEqual(audit.events[-1]["action"], "model.response")
        self.assertEqual(audit.events[-1]["outcome"], "denied")
        self.assertEqual(
            audit.events[-1]["detail"]["reason"],
            "token_estimate_invalid_or_context_window_exceeded",
        )


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

            async def complete(self, *, config, messages, request_parameters, timeout_seconds, tools=None, tool_choice=None, on_token=None):
                self.calls.append((config.provider_id, config.model_id))
                return {
                    "choices": [{"message": {"content": "budget answer"}}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 10},
                }

        estimator = RecordingEstimator()
        transport = SuccessTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=AuditRecorder(),
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
            audit_sink=AuditRecorder(),
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


    async def test_estimator_receives_effective_template_and_call_parameters(self):
        class RecordingEstimator:
            def __init__(self):
                self.arguments = None

            def estimate(self, **kwargs):
                self.arguments = kwargs
                return SimpleNamespace(input_tokens=4, output_tokens=10, total_tokens=14)

        class SuccessTransport:
            def __init__(self):
                self.calls = []

            async def complete(self, *, config, messages, request_parameters, timeout_seconds, tools=None, tool_choice=None, on_token=None):
                self.calls.append((config.model_id, dict(config.parameters), dict(request_parameters)))
                return {
                    "choices": [{"message": {"content": "ok"}}],
                    "usage": {"prompt_tokens": 4, "completion_tokens": 2},
                }

        snapshot = self.snapshot()
        snapshot.entries[0]["definition"]["parameters"] = {"max_tokens": 10}
        estimator = RecordingEstimator()
        transport = SuccessTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=AuditRecorder(),
            resolver=RegistryModelResolver(Secrets()),
            transport=transport,
            token_estimator=estimator,
        )
        await router.complete(
            snapshot=snapshot, tenant_id="tenant-a", run_id="run-effective-params",
            template_id="primary",
            messages=({"role": "user", "content": "hello"},),
            request_id="request-effective-params",
        )
        self.assertEqual(estimator.arguments["request_parameters"], {"max_tokens": 10})
        self.assertEqual(estimator.arguments["context_window_tokens"], 8192)
        self.assertEqual(transport.calls[0][1], {"max_tokens": 10})
        self.assertEqual(transport.calls[0][2], {})

    async def test_invalid_estimate_exceeding_context_window_blocks_provider(self):
        class OversizedEstimator:
            def estimate(self, **kwargs):
                return SimpleNamespace(input_tokens=10, output_tokens=10, total_tokens=20)

        snapshot = self.snapshot()
        snapshot.entries[0]["definition"]["context_window_tokens"] = 16
        audit = AuditRecorder()
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit,
            resolver=RegistryModelResolver(Secrets()),
            transport=transport,
            token_estimator=OversizedEstimator(),
        )
        with self.assertRaisesRegex(ModelRoutingError, "exceeds the pinned context window"):
            await router.complete(
                snapshot=snapshot, tenant_id="tenant-a", run_id="run-window-limit",
                template_id="primary", fallback_template_ids=("fallback",),
                messages=({"role": "user", "content": "hello"},),
                request_id="request-window-limit",
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(audit.events[-1]["detail"]["reason"],
                         "token_estimate_invalid_or_context_window_exceeded")


    async def test_truncate_policy_preserves_run_anchors_before_provider_dispatch(self):
        class LengthEstimator:
            def estimate(self, *, model_id, messages, request_parameters, context_window_tokens):
                input_tokens = sum(len(str(message.get("content", ""))) for message in messages)
                output_tokens = request_parameters["max_tokens"]
                total = input_tokens + output_tokens
                if total > context_window_tokens:
                    raise ValueError("estimated prompt plus planned output exceeds model context window")
                return SimpleNamespace(
                    input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total,
                )

        class CaptureTransport:
            def __init__(self):
                self.messages = None

            async def complete(self, *, config, messages, request_parameters, timeout_seconds, tools=None, tool_choice=None, on_token=None):
                self.messages = tuple(messages)
                return {
                    "choices": [{"message": {"content": "done"}}],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 2},
                }

        audit = AuditRecorder()
        transport = CaptureTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit, resolver=RegistryModelResolver(Secrets()),
            transport=transport, token_estimator=LengthEstimator(),
            context_overflow_policy="truncate",
        )
        await router.complete(
            snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-overflow",
            template_id="primary", request_id="request-overflow",
            messages=(
                {"role": "system", "content": "Keep platform rules."},
                {"role": "user", "content": "old history " * 1000},
                {"role": "assistant", "content": "old response " * 1000},
                {"role": "user", "content": "Latest user request."},
            ),
            request_parameters={"max_tokens": 10},
            protected_context={
                "goal": "finish reconciliation", "constraints": ["read only"],
                "decisions": ["use FIFO"], "evidence": ["approved source snapshot"],
            },
        )
        dispatched = "\\n".join(str(message.get("content", "")) for message in transport.messages)
        self.assertIn("Latest user request.", dispatched)
        self.assertIn("finish reconciliation", dispatched)
        self.assertIn("read only", dispatched)
        self.assertIn("use FIFO", dispatched)
        self.assertIn("approved source snapshot", dispatched)
        self.assertNotIn("old history", dispatched)
        self.assertTrue(any(
            event["action"] == "model.context_overflow_resolved"
            and event["detail"]["policy"] == "truncate"
            for event in audit.events
        ))

    async def test_human_intervention_policy_without_operator_blocks_provider_dispatch(self):
        class OversizedEstimator:
            def estimate(self, **kwargs):
                return SimpleNamespace(input_tokens=9000, output_tokens=10, total_tokens=9010)

        transport = FallbackTransport()
        audit = AuditRecorder()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit, resolver=RegistryModelResolver(Secrets()),
            transport=transport, token_estimator=OversizedEstimator(),
            context_overflow_policy="human_intervention",
        )
        with self.assertRaisesRegex(ModelRoutingError, "requires human intervention"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-human",
                template_id="primary", fallback_template_ids=("fallback",),
                messages=({"role": "user", "content": "oversized"},),
                request_id="request-human", request_parameters={"max_tokens": 10},
                protected_context={
                    "goal": "goal", "constraints": ["read only"],
                    "decisions": ["no automatic approval"], "evidence": ["source ticket"],
                },
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(audit.events[-1]["detail"]["reason"], "human_intervention_required")



    async def test_quota_cost_estimation_failure_is_audited_before_provider_dispatch(self):
        class TokenEstimator:
            def estimate(self, **kwargs):
                return SimpleNamespace(input_tokens=4, output_tokens=8, total_tokens=12)

        class BrokenCostEstimator:
            async def estimate_micros(self, **kwargs):
                raise TimeoutError("pricing backend unavailable")

        class NeverReserve:
            async def reserve_run(self, **kwargs):
                raise AssertionError("reservation must not be attempted")

        audit = AuditRecorder()
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit, resolver=RegistryModelResolver(Secrets()),
            transport=transport, quota_service=NeverReserve(),
            token_estimator=TokenEstimator(), quota_cost_estimator=BrokenCostEstimator(),
        )
        with self.assertRaises(TimeoutError):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-price-fail",
                template_id="primary", messages=({"role": "user", "content": "hello"},),
                request_id="request-price-fail",
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(audit.events[-1]["action"], "model.response")
        self.assertEqual(audit.events[-1]["outcome"], "failed")
        self.assertEqual(audit.events[-1]["detail"]["reason"], "quota_cost_estimation_failed")
        self.assertEqual(audit.events[-1]["detail"]["error_type"], "TimeoutError")

    async def test_quota_scope_resolution_failure_is_audited_before_provider_dispatch(self):
        class TokenEstimator:
            def estimate(self, **kwargs):
                return SimpleNamespace(input_tokens=4, output_tokens=8, total_tokens=12)

        class CostEstimator:
            async def estimate_micros(self, **kwargs):
                return 1000

        class NeverReserve:
            async def reserve_run(self, **kwargs):
                raise AssertionError("reservation must not be attempted")

        def broken_scope(_snapshot, _config):
            raise PermissionError("scope resolver unavailable")

        audit = AuditRecorder()
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit, resolver=RegistryModelResolver(Secrets()),
            transport=transport, quota_service=NeverReserve(),
            token_estimator=TokenEstimator(), quota_cost_estimator=CostEstimator(),
            quota_scope_resolver=broken_scope,
        )
        with self.assertRaises(PermissionError):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-scope-fail",
                template_id="primary", messages=({"role": "user", "content": "hello"},),
                request_id="request-scope-fail",
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(audit.events[-1]["outcome"], "failed")
        self.assertEqual(audit.events[-1]["detail"]["reason"], "quota_scope_resolution_failed")

    async def test_quota_reservation_denial_is_audited_and_blocks_provider_dispatch(self):
        class TokenEstimator:
            def estimate(self, **kwargs):
                return SimpleNamespace(input_tokens=4, output_tokens=8, total_tokens=12)

        class CostEstimator:
            async def estimate_micros(self, **kwargs):
                return 1000

        class RejectingQuotaService:
            async def reserve_run(self, **kwargs):
                raise BudgetExceeded("hard quota exceeded")

        audit = AuditRecorder()
        transport = FallbackTransport()
        router = OpenAICompatibleModelRouter(
            audit_sink=audit, resolver=RegistryModelResolver(Secrets()),
            transport=transport, quota_service=RejectingQuotaService(),
            token_estimator=TokenEstimator(), quota_cost_estimator=CostEstimator(),
        )
        with self.assertRaises(BudgetExceeded):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-quota-denied",
                template_id="primary", messages=({"role": "user", "content": "hello"},),
                request_id="request-quota-denied",
            )
        self.assertEqual(transport.calls, [])
        self.assertEqual(audit.events[-1]["outcome"], "denied")
        self.assertEqual(audit.events[-1]["detail"]["reason"], "quota_reservation_denied")
    async def test_streaming_callback_is_forwarded_without_bypassing_router_audit(self):
        class StreamingTransport:
            def __init__(self):
                self.streamed = False

            async def complete(self, *, config, messages, request_parameters,
                               timeout_seconds, tools=None, tool_choice=None, on_token=None):
                self.streamed = on_token is not None
                if on_token is not None:
                    await on_token("first")
                    await on_token("second")
                return {
                    "choices": [{"message": {"content": "firstsecond"}}],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 2},
                }

        transport = StreamingTransport()
        audit = AuditRecorder()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=transport, audit_sink=audit,
        )
        tokens = []

        async def on_token(token):
            tokens.append(token)

        result = await router.complete(
            snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-stream",
            template_id="primary",
            messages=({"role": "user", "content": "hello"},),
            request_id="request-stream", on_token=on_token,
        )
        self.assertEqual(tokens, ["first", "second"])
        self.assertTrue(transport.streamed)
        self.assertEqual(result.content, "firstsecond")
        self.assertEqual(audit.events[-1]["action"], "model.response")
        self.assertEqual(audit.events[-1]["outcome"], "succeeded")

    async def test_partial_stream_failure_never_falls_back_after_visible_tokens(self):
        class PartialFailureTransport:
            def __init__(self):
                self.calls = 0

            async def complete(self, *, config, messages, request_parameters,
                               timeout_seconds, tools=None, tool_choice=None, on_token=None):
                self.calls += 1
                if on_token is not None:
                    await on_token("already-visible")
                raise ModelTransportError("connection lost after first delta")

        transport = PartialFailureTransport()
        router = OpenAICompatibleModelRouter(
            resolver=RegistryModelResolver(Secrets()),
            transport=transport, audit_sink=AuditRecorder(),
        )
        tokens = []

        async def on_token(token):
            tokens.append(token)

        with self.assertRaisesRegex(ModelUsageRecordingError, "fallback is prohibited"):
            await router.complete(
                snapshot=self.snapshot(), tenant_id="tenant-a", run_id="run-partial",
                template_id="primary", fallback_template_ids=("fallback",),
                messages=({"role": "user", "content": "hello"},),
                request_id="request-partial", on_token=on_token,
            )
        self.assertEqual(tokens, ["already-visible"])
        self.assertEqual(transport.calls, 1)


if __name__ == "__main__":
    unittest.main()
