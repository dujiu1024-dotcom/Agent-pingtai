import unittest

from agent_platform.observability.metrics import InMemoryMetricsRecorder


class InMemoryMetricsSafetyTests(unittest.TestCase):
    def test_prometheus_label_values_are_escaped(self):
        metrics = InMemoryMetricsRecorder()
        metrics.increment(
            "agent_runs_total",
            labels={"component": 'api"\\worker\nnext'},
        )
        rendered = metrics.render_prometheus()
        self.assertEqual(
            rendered,
            'agent_runs_total{component="api\\"\\\\worker\\nnext"} 1.0\n',
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


if __name__ == "__main__":
    unittest.main()
