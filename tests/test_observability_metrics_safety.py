import unittest

from agent_platform.observability.metrics import InMemoryMetricsRecorder


class InMemoryMetricsSafetyTests(unittest.TestCase):
    def test_prometheus_label_values_are_escaped(self):
        metrics = InMemoryMetricsRecorder()
        metrics.increment(
            "agent_runs_total",
            labels={"component": 'api"\\worker\\nnext'},
        )
        rendered = metrics.render_prometheus()
        self.assertEqual(
            rendered,
            'agent_runs_total{component="api\\\"\\\\worker\\nnext"} 1.0\\n',
        )

    def test_non_finite_and_non_numeric_values_are_rejected(self):
        metrics = InMemoryMetricsRecorder()
        for value in (float("nan"), float("inf"), float("-inf"), True, "1"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                metrics.increment("agent_runs_total", value)
            with self.subTest(observation=value), self.assertRaises(ValueError):
                metrics.observe("agent_run_duration_seconds", value)

    def test_negative_values_are_rejected(self):
        metrics = InMemoryMetricsRecorder()
        with self.assertRaisesRegex(ValueError, "non-negative"):
            metrics.increment("agent_runs_total", -1)
        with self.assertRaisesRegex(ValueError, "non-negative"):
            metrics.observe("agent_run_duration_seconds", -0.1)

    def test_excess_high_cardinality_labels_are_rejected_without_recording(self):
        operations = (
            lambda metrics, labels: metrics.increment("agent_runs_total", labels=labels),
            lambda metrics, labels: metrics.observe("agent_run_duration_seconds", 0.25, labels=labels),
            lambda metrics, labels: metrics.set_gauge("queue_depth", 3, labels=labels),
        )
        bounded_labels = {
            "component": "worker",
            "status": "succeeded",
            "model_template": "chat-v2",
            "finish_reason": "stop",
            "operation": "execute",
            "queue": "default",
            "run_id": "run-unique-123",
        }

        for operation in operations:
            with self.subTest(operation=operation):
                metrics = InMemoryMetricsRecorder()
                with self.assertRaisesRegex(ValueError, "high-cardinality or unsupported"):
                    operation(metrics, bounded_labels)

                snapshot = metrics.snapshot()
                self.assertEqual(snapshot["counters"], {})
                self.assertEqual(snapshot["observation_counts"], {})
                self.assertEqual(snapshot["gauges"], {})
                self.assertEqual(metrics.render_prometheus(), "")


if __name__ == "__main__":
    unittest.main()
