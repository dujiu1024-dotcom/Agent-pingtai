import unittest
from contextlib import contextmanager
from decimal import Decimal

from agent_platform.contracts import ModelTemplateRef, RunContext
from agent_platform.observability.cost import ModelCostEstimator, ModelPricing
from agent_platform.observability.evaluation import (
    EvalCase,
    EvalDataset,
    EvalGatePolicy,
    build_report,
    evaluate_case,
    gate_report,
)
from agent_platform.observability.harness import ObservedHarnessAdapter
from agent_platform.observability.langfuse import LangfuseObservationSink
from agent_platform.observability.metrics import InMemoryMetricsRecorder
from agent_platform.observability.prometheus import PrometheusClientMetricsRecorder
from agent_platform.observability.prompts import PromptRegistry
from agent_platform.observability.redaction import redact_payload, redact_text
from agent_platform.runtime.protocols import HarnessRequest, HarnessResult


class FakeHarness:
    async def run(self, request):
        return HarnessResult(output_text="approved answer", usage={"input_tokens": 12, "output_tokens": 8})


class FakeSpan:
    def __init__(self):
        self.updates = []

    def update(self, **kwargs):
        self.updates.append(kwargs)


class FakeObserver:
    def __init__(self):
        self.calls = []
        self.span = FakeSpan()

    @contextmanager
    def observe(self, *, name, input, metadata):
        self.calls.append((name, input, metadata))
        yield self.span


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_harness_adapter_records_trace_and_low_cardinality_metrics(self):
        observer = FakeObserver()
        metrics = InMemoryMetricsRecorder()
        request = HarnessRequest(
            context=RunContext(
                tenant_id="tenant-a", run_id="run-a", agent_id="agent-a",
                model_template=ModelTemplateRef("general", 2),
            ),
            instructions="Do the task", input_text="hello Bearer abc.def.ghi",
        )
        adapter = ObservedHarnessAdapter(
            FakeHarness(), observer=observer, metrics=metrics, capture_content=True,
            cost_estimator=ModelCostEstimator({
                "general@2": ModelPricing(Decimal("1.0"), Decimal("2.0")),
            }),
        )
        result = await adapter.run(request)
        self.assertEqual(result.output_text, "approved answer")
        self.assertEqual(observer.calls[0][0], "agent.run")
        self.assertIn("[REDACTED]", observer.calls[0][1]["input_text"])
        self.assertEqual(observer.calls[0][2]["run_id"], "run-a")
        self.assertEqual(len(observer.span.updates), 1)
        self.assertIn("estimated_cost_usd", observer.span.updates[0]["output"])
        snapshot = metrics.snapshot()
        self.assertIn("agent_estimated_cost_usd_total", " ".join(snapshot["counters"]))
        self.assertIn('agent_runs_total{component="harness",model_template="general@2",status="succeeded"}', snapshot["counters"])
        self.assertIn('agent_run_duration_seconds{component="harness",model_template="general@2"}', snapshot["observation_counts"])

    async def test_telemetry_backend_failure_does_not_break_harness(self):
        class BrokenObserver:
            def observe(self, **kwargs):
                raise RuntimeError("telemetry unavailable")

        class BrokenMetrics:
            def increment(self, *args, **kwargs):
                raise RuntimeError("metrics unavailable")

            def observe(self, *args, **kwargs):
                raise RuntimeError("metrics unavailable")

        request = HarnessRequest(
            context=RunContext("tenant-a", "run-safe", "agent-a", ModelTemplateRef("general")),
            instructions="", input_text="",
        )
        adapter = ObservedHarnessAdapter(
            FakeHarness(), observer=BrokenObserver(), metrics=BrokenMetrics(),
        )
        result = await adapter.run(request)
        self.assertEqual(result.output_text, "approved answer")

    async def test_harness_failure_is_recorded_and_reraised(self):
        class BrokenHarness:
            async def run(self, request):
                raise RuntimeError("provider unavailable")

        metrics = InMemoryMetricsRecorder()
        request = HarnessRequest(
            context=RunContext("tenant-a", "run-b", "agent-a", ModelTemplateRef("general")),
            instructions="", input_text="",
        )
        adapter = ObservedHarnessAdapter(BrokenHarness(), metrics=metrics)
        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            await adapter.run(request)
        self.assertTrue(any('status="failed"' in key for key in metrics.snapshot()["counters"]))

    def test_langfuse_sink_uses_sdk_v3_observation_contract(self):
        class FakeClient:
            def __init__(self):
                self.args = None

            @contextmanager
            def start_as_current_observation(self, **kwargs):
                self.args = kwargs
                yield FakeSpan()

        client = FakeClient()
        sink = LangfuseObservationSink(client=client)
        with sink.observe(name="agent.run", input={"input_chars": 3}, metadata={"run_id": "run-1"}):
            pass
        self.assertEqual(client.args["as_type"], "span")
        self.assertEqual(client.args["name"], "agent.run")
        self.assertEqual(client.args["metadata"]["run_id"], "run-1")

    def test_redaction_recurses_and_truncates(self):
        payload = {"api_key": "secret-value", "nested": ["Authorization: Bearer abc.def", "sk-12345678901234567890"]}
        result = redact_payload(payload, max_text_chars=100)
        self.assertEqual(result["api_key"], "[REDACTED]")
        self.assertIn("Bearer [REDACTED]", result["nested"][0])
        self.assertEqual(result["nested"][1], "[REDACTED]")
        self.assertTrue(redact_text("x" * 20, max_chars=5).endswith("[TRUNCATED]"))

    def test_metrics_reject_high_cardinality_labels_and_render_lines(self):
        metrics = InMemoryMetricsRecorder()
        with self.assertRaisesRegex(ValueError, "high-cardinality"):
            metrics.increment("agent_runs_total", labels={"run_id": "run-1"})
        metrics.increment("agent_runs_total", labels={"status": "ok"})
        exposition = metrics.render_prometheus()
        self.assertTrue(exposition.endswith("\n"))
        # 计数器名本身已带 _total（与 docs/architecture 的指标契约一致），
        # Prometheus 暴露时不再追加 _count；_count 只用于观测值的条数。
        self.assertIn('agent_runs_total{status="ok"}', exposition)

    def test_agent_and_rag_evaluation_gate(self):
        dataset = EvalDataset("smoke", 1, (
            EvalCase("case-1", expected_terms=("青岛",), expected_document_ids=("doc-1",)),
            EvalCase("case-2", expected_terms=("库存",), forbidden_terms=("未验证",)),
        ))
        first = evaluate_case(dataset.cases[0], output_text="青岛库存分析", retrieved_document_ids=("doc-1", "doc-2"))
        second = evaluate_case(dataset.cases[1], output_text="库存结果已核验")
        report = build_report(dataset, (first, second))
        allowed, reasons = gate_report(report, EvalGatePolicy(min_pass_rate=1.0, min_average_score=0.9))
        self.assertTrue(allowed)
        self.assertEqual(reasons, ())

    def test_prompt_versions_validate_variables_canary_and_rollback(self):
        registry = PromptRegistry()
        v1 = registry.register("answer", "Answer {question}")
        registry.activate("answer", v1.version)
        v2 = registry.register("answer", "Answer carefully: {question}")
        registry.set_canary("answer", v2.version, 100)
        rendered, selected = registry.render("answer", {"question": "why"}, rollout_key="run-1")
        self.assertEqual(selected.version, v2.version)
        self.assertEqual(rendered, "Answer carefully: why")
        registry.activate("answer", v1.version)
        rendered, selected = registry.render("answer", {"question": "why"})
        self.assertEqual(selected.version, v1.version)
        with self.assertRaisesRegex(ValueError, "variables mismatch"):
            registry.render("answer", {"unexpected": "value"})
        with self.assertRaisesRegex(ValueError, "simple variable"):
            registry.register("unsafe", "Value {user.name}")

    def test_evaluation_gate_blocks_regression(self):
        case = EvalCase("case-1", expected_terms=("expected",))
        result = evaluate_case(case, output_text="wrong answer")
        report = build_report(EvalDataset("regression", 1, (case,)), (result,))
        allowed, reasons = gate_report(report, EvalGatePolicy())
        self.assertFalse(allowed)
        self.assertIn("pass_rate_below_threshold", reasons)


    def test_prometheus_client_recorder_exports_counters_and_histograms(self):
        metrics = PrometheusClientMetricsRecorder()
        metrics.increment(
            "agent_runs_total", 2,
            labels={"component": "harness", "status": "succeeded"},
        )
        metrics.observe(
            "agent_run_duration_seconds", 0.25,
            labels={"component": "harness"},
        )
        exposition = metrics.render_prometheus()
        self.assertIn('agent_runs_total{component="harness",status="succeeded"} 2.0', exposition)
        self.assertIn('agent_run_duration_seconds_count{component="harness"} 1.0', exposition)
        self.assertIn('agent_run_duration_seconds_sum{component="harness"} 0.25', exposition)
        self.assertIn("agent_run_duration_seconds_bucket", exposition)

    def test_prometheus_client_recorder_rejects_cardinality_and_schema_drift(self):
        metrics = PrometheusClientMetricsRecorder()
        with self.assertRaisesRegex(ValueError, "high-cardinality"):
            metrics.increment("agent_runs_total", labels={"tenant_id": "tenant-a"})
        metrics.increment("agent_runs_total", labels={"status": "ok"})
        with self.assertRaisesRegex(ValueError, "label names must remain stable"):
            metrics.increment("agent_runs_total", labels={"component": "harness"})
        with self.assertRaisesRegex(ValueError, "metric type and label names"):
            metrics.observe("agent_runs_total", 0.1, labels={"status": "ok"})
        with self.assertRaisesRegex(ValueError, "non-negative"):
            metrics.increment("negative_counter", -1)
        with self.assertRaisesRegex(ValueError, "finite number"):
            metrics.observe("not_finite", float("nan"))


if __name__ == "__main__":
    unittest.main()
