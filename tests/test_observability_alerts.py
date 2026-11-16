from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_platform.api.observability import create_observability_router
from agent_platform.identity.models import Principal
from agent_platform.observability.alerts import AlertManager, AlertRule
from agent_platform.observability.metrics import InMemoryMetricsRecorder


class FakeNotifier:
    def __init__(self):
        self.events = []

    def notify(self, event):
        self.events.append(event)


class AlertTests(unittest.TestCase):
    def test_alert_fires_once_per_cooldown_and_rearms(self):
        now = [datetime(2026, 11, 16, tzinfo=timezone.utc)]
        notifier = FakeNotifier()
        manager = AlertManager(
            (AlertRule("queue-backlog", "queue_depth", 100,
                       severity="critical", cooldown=timedelta(minutes=5)),),
            notifier, clock=lambda: now[0],
        )
        self.assertEqual(len(manager.evaluate({"queue_depth": 120})), 1)
        self.assertEqual(manager.evaluate({"queue_depth": 150}), ())
        now[0] += timedelta(minutes=6)
        self.assertEqual(len(manager.evaluate({"queue_depth": 101})), 1)
        self.assertEqual(len(notifier.events), 2)

    def test_invalid_rule_and_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            AlertRule("x", "metric", 1, comparison="approx")
        rule = AlertRule("same", "metric", 1)
        with self.assertRaises(ValueError):
            AlertManager((rule, rule), FakeNotifier())


class MetricsEndpointTests(unittest.TestCase):
    def test_metrics_endpoint_requires_authenticated_user_and_exports_prometheus(self):
        metrics = InMemoryMetricsRecorder()
        metrics.increment("agent_runs_total", labels={"status": "succeeded"})
        app = FastAPI()
        app.include_router(create_observability_router(metrics=metrics))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="u", tenant_id="tenant-a", session_id="s",
                    email="u@example.test", display_name="User",
                )
            })()
            return await call_next(request)

        response = TestClient(app).get("/api/platform/v1/observability/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertIn('agent_runs_total{status="succeeded"} 1.0', response.text)
        self.assertIn("text/plain", response.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
