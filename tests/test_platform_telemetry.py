import json
import logging
import unittest

from agent_platform.observability.metrics import InMemoryMetricsRecorder
from agent_platform.observability.telemetry import PlatformTelemetry, StructuredEventLogger, bounded_labels


class PlatformTelemetryTests(unittest.TestCase):
    def test_model_tokens_cost_and_operation_metrics(self):
        metrics = InMemoryMetricsRecorder()
        telemetry = PlatformTelemetry(metrics)
        telemetry.model_usage(input_tokens=120, output_tokens=30, cost_usd=0.004, model_template="chat-v2")
        telemetry.operation(component="api", operation="execute", status="succeeded", duration_seconds=0.25)
        counters = metrics.snapshot()["counters"]
        self.assertEqual(counters['model_tokens_total{component="model",model_template="chat-v2",operation="complete",status="succeeded"}'], 150.0)
        self.assertEqual(counters['model_cost_usd_total{component="model",model_template="chat-v2",operation="complete",status="succeeded"}'], 0.004)
        self.assertIn("operations_total", " ".join(counters))

    def test_retry_tool_failure_rejection_and_queue_backlog(self):
        metrics = InMemoryMetricsRecorder()
        telemetry = PlatformTelemetry(metrics)
        telemetry.retry(component="worker", operation="retry")
        telemetry.tool_failure()
        telemetry.operation(component="api", operation="invoke", status="denied", duration_seconds=0)
        telemetry.queue_depth(depth=17, queue="background")
        snapshot = metrics.snapshot()
        self.assertTrue(any(key.startswith("operation_retries_total") for key in snapshot["counters"]))
        self.assertTrue(any(key.startswith("tool_failures_total") for key in snapshot["counters"]))
        self.assertTrue(any(key.startswith("operation_rejections_total") for key in snapshot["counters"]))
        self.assertTrue(any(key.startswith('queue_depth{component="queue",operation="poll",queue="background"}') for key in snapshot["observation_counts"]))

    def test_high_cardinality_and_invalid_values_rejected(self):
        with self.assertRaises(ValueError):
            bounded_labels(component="run", operation="execute", status="succeeded", model_template="run-123")
        metrics = InMemoryMetricsRecorder()
        with self.assertRaises(ValueError):
            PlatformTelemetry(metrics).model_usage(input_tokens=-1, output_tokens=0)
        with self.assertRaises(ValueError):
            PlatformTelemetry(metrics).queue_depth(depth=1, queue="x" * 65)

    def test_structured_json_redacts_secrets_and_rejects_payload_fields(self):
        class Capture(logging.Handler):
            def __init__(self):
                super().__init__()
                self.message = None
            def emit(self, record):
                self.message = record.getMessage()

        logger = logging.Logger("telemetry-test")
        capture = Capture()
        logger.addHandler(capture)
        StructuredEventLogger(logger).emit(
            "tool.failed", component="tool", operation="invoke", status="failed",
            fields={"error_type": "RuntimeError", "trace_id": "abc", "span_id": "def"},
        )
        event = json.loads(capture.message)
        self.assertEqual(event["event"], "tool.failed")
        with self.assertRaises(ValueError):
            StructuredEventLogger(logger).emit(
                "unsafe", component="tool", operation="invoke", status="failed",
                fields={"prompt": "private content"},
            )


if __name__ == "__main__":
    unittest.main()
