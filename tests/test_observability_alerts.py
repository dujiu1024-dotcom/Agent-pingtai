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
        self.assertEqual(manager.evaluate({"queue_depth": float("nan")}), ())
        self.assertEqual(manager.evaluate({"queue_depth": 150}), ())
        now[0] += timedelta(minutes=6)
        self.assertEqual(len(manager.evaluate({"queue_depth": 101})), 1)
        self.assertEqual(len(notifier.events), 2)

    def test_invalid_rule_and_duplicate_ids_are_rejected(self):
        with self.assertRaises(ValueError):
            AlertRule("x", "metric", 1, comparison="approx")
        with self.assertRaisesRegex(ValueError, "finite"):
            AlertRule("nan", "metric", float("nan"))
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


class AlertApiTests(unittest.TestCase):
    def _app(self, store, authorize=lambda actor: True):
        app = FastAPI()
        app.include_router(create_observability_router(
            metrics=InMemoryMetricsRecorder(), alert_store=store,
            authorize_alert_manage=authorize,
        ))

        @app.middleware("http")
        async def attach_principal(request, call_next):
            request.state.identity = type("Identity", (), {
                "principal": Principal(
                    user_id="operator-1", tenant_id="tenant-a", session_id="s",
                    email="u@example.test", display_name="Operator",
                )
            })()
            return await call_next(request)
        return app

    def test_alert_list_is_tenant_scoped_and_ack_requires_permission(self):
        class Store:
            def __init__(self):
                self.scope = None
                self.actor = None
            async def list_open(self, *, scope_key, limit):
                self.scope = scope_key
                return [{"event_id": "evt-1", "status": "open"}]
            async def acknowledge(self, event_id, *, scope_key, actor_id, now):
                self.scope, self.actor = scope_key, actor_id
                return True
            async def resolve(self, event_id, *, scope_key, actor_id, now):
                self.scope, self.actor = scope_key, actor_id
                return True

        store = Store()
        client = TestClient(self._app(store, authorize=lambda actor: False))
        response = client.get("/api/platform/v1/observability/alerts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(store.scope, "tenant-a")
        denied = client.post("/api/platform/v1/observability/alerts/evt-1/acknowledge")
        self.assertEqual(denied.status_code, 403)
        self.assertIsNone(store.actor)

        allowed_client = TestClient(self._app(store))
        acknowledged = allowed_client.post(
            "/api/platform/v1/observability/alerts/evt-1/acknowledge"
        )
        self.assertEqual(acknowledged.status_code, 200)
        self.assertEqual(acknowledged.json()["status"], "acknowledged")
        self.assertEqual(store.scope, "tenant-a")
        self.assertEqual(store.actor, "operator-1")

    def test_alert_operations_fail_closed_when_store_or_event_missing(self):
        client = TestClient(self._app(None))
        self.assertEqual(
            client.get("/api/platform/v1/observability/alerts").status_code, 503
        )

        class Store:
            async def list_open(self, **kwargs):
                return []
            async def acknowledge(self, *args, **kwargs):
                return False
            async def resolve(self, *args, **kwargs):
                return False

        client = TestClient(self._app(Store()))
        self.assertEqual(
            client.post("/api/platform/v1/observability/alerts/missing/resolve").status_code,
            404,
        )


if __name__ == "__main__":
    unittest.main()
