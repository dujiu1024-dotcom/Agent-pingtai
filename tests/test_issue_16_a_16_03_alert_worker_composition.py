from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_platform.observability.production_alert_worker import (
    AlertDispatchSettings,
    build_alert_dispatch_worker,
    load_alert_dispatch_settings,
)


class FakeWebhookClient:
    async def post(self, url, *, json, headers, timeout):
        raise AssertionError("composition must not send notifications during construction")


class ProductionAlertWorkerCompositionTests(unittest.TestCase):
    def test_loads_endpoint_allowlist_and_bounded_polling_configuration(self):
        settings = load_alert_dispatch_settings({
            "ALERT_WEBHOOK_URL": "https://alerts.example.com/hooks/platform",
            "ALERT_WEBHOOK_ALLOWED_HOSTS": "alerts.example.com",
            "ALERT_DISPATCH_POLL_INTERVAL_SECONDS": "2.5",
            "ALERT_DISPATCH_BATCH_SIZE": "25",
        })
        self.assertEqual(settings.endpoint, "https://alerts.example.com/hooks/platform")
        self.assertEqual(settings.allowed_hosts, ("alerts.example.com",))
        self.assertEqual(settings.poll_interval_seconds, 2.5)
        self.assertEqual(settings.batch_size, 25)

    def test_reads_authorization_only_from_secret_file(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = Path(directory) / "webhook-token"
            secret.write_text("Bearer secret-token\n", encoding="utf-8")
            settings = load_alert_dispatch_settings({
                "ALERT_WEBHOOK_URL": "https://alerts.example.com/hooks/platform",
                "ALERT_WEBHOOK_ALLOWED_HOSTS": "alerts.example.com",
                "ALERT_WEBHOOK_AUTHORIZATION_FILE": str(secret),
            })
        self.assertEqual(settings.authorization_header, "Bearer secret-token")

    def test_rejects_missing_endpoint_invalid_hosts_and_unbounded_polling(self):
        for environ in (
            {},
            {"ALERT_WEBHOOK_URL": "https://alerts.example.com/hook",
             "ALERT_WEBHOOK_ALLOWED_HOSTS": ""},
            {"ALERT_WEBHOOK_URL": "https://alerts.example.com/hook",
             "ALERT_WEBHOOK_ALLOWED_HOSTS": "alerts.example.com,alerts.example.com"},
            {"ALERT_WEBHOOK_URL": "https://alerts.example.com/hook",
             "ALERT_WEBHOOK_ALLOWED_HOSTS": "alerts.example.com",
             "ALERT_DISPATCH_POLL_INTERVAL_SECONDS": "0"},
            {"ALERT_WEBHOOK_URL": "https://alerts.example.com/hook",
             "ALERT_WEBHOOK_ALLOWED_HOSTS": "alerts.example.com",
             "ALERT_DISPATCH_BATCH_SIZE": "501"},
        ):
            with self.subTest(environ=environ), self.assertRaises(ValueError):
                load_alert_dispatch_settings(environ)

    def test_composes_postgres_outbox_audit_provider_and_worker_without_side_effects(self):
        async def connection_factory():
            raise AssertionError("no database connection is opened during composition")

        settings = AlertDispatchSettings(
            endpoint="https://alerts.example.com/hooks/platform",
            allowed_hosts=("alerts.example.com",),
            authorization_header="Bearer test-token",
            poll_interval_seconds=3,
            batch_size=17,
        )
        worker = build_alert_dispatch_worker(
            connection_factory=connection_factory,
            webhook_client=FakeWebhookClient(),
            settings=settings,
        )
        self.assertEqual(worker.poll_interval_seconds, 3)
        self.assertEqual(worker.batch_size, 17)
        self.assertEqual(worker.dispatcher.store._connect, connection_factory)
        self.assertEqual(worker.dispatcher.provider.endpoint, settings.endpoint)
        self.assertEqual(worker.dispatcher.provider.authorization_header, "Bearer test-token")
        self.assertTrue(callable(worker.dispatcher.audit_sink.record))


if __name__ == "__main__":
    unittest.main()
