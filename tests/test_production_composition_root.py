from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from agent_platform.events.outbox_worker import OutboxRelayWorker
from agent_platform.production_config import (
    ProductionConfigurationError,
    load_production_settings,
)


class ProductionConfigurationTests(unittest.TestCase):
    def test_loads_compose_style_file_secrets_and_exact_origins(self):
        with TemporaryDirectory() as directory:
            postgres_secret = Path(directory) / "postgres"
            redis_secret = Path(directory) / "redis"
            postgres_secret.write_text("p@ss/word", encoding="utf-8")
            redis_secret.write_text("redis-secret", encoding="utf-8")
            settings = load_production_settings({
                "POSTGRES_HOST": "postgres",
                "POSTGRES_PORT": "5432",
                "POSTGRES_DB": "agent platform",
                "POSTGRES_USER": "agent",
                "POSTGRES_PASSWORD_FILE": str(postgres_secret),
                "REDIS_HOST": "redis",
                "REDIS_PORT": "6379",
                "REDIS_PASSWORD_FILE": str(redis_secret),
                "ALLOWED_ORIGINS": "https://platform.example.com, https://admin.example.com",
            })
        self.assertIn("p%40ss%2Fword", settings.postgres_dsn)
        self.assertIn("agent%20platform", settings.postgres_dsn)
        self.assertIn("redis-secret", settings.redis_url)
        self.assertEqual(settings.allowed_origins, (
            "https://platform.example.com", "https://admin.example.com",
        ))

    def test_accepts_explicit_dsn_and_redis_url(self):
        settings = load_production_settings({
            "AGENT_PLATFORM_DSN": "postgresql://platform@db/platform",
            "REDIS_URL": "redis://redis:6379/2",
            "ALLOWED_ORIGINS": "https://platform.example.com",
        })
        self.assertEqual(settings.postgres_dsn, "postgresql://platform@db/platform")
        self.assertEqual(settings.redis_url, "redis://redis:6379/2")

    def test_missing_database_redis_or_origin_configuration_fails_fast(self):
        with self.assertRaisesRegex(ProductionConfigurationError, "POSTGRES_HOST"):
            load_production_settings({})
        base = {
            "AGENT_PLATFORM_DSN": "postgresql://platform@db/platform",
            "REDIS_URL": "redis://redis:6379/0",
        }
        with self.assertRaisesRegex(ProductionConfigurationError, "ALLOWED_ORIGINS"):
            load_production_settings(base)

    def test_secret_file_and_direct_secret_are_mutually_exclusive(self):
        with TemporaryDirectory() as directory:
            secret = Path(directory) / "secret"
            secret.write_text("secret", encoding="utf-8")
            with self.assertRaisesRegex(ProductionConfigurationError, "only one"):
                load_production_settings({
                    "POSTGRES_HOST": "postgres",
                    "POSTGRES_DB": "platform",
                    "POSTGRES_USER": "platform",
                    "POSTGRES_PASSWORD": "direct",
                    "POSTGRES_PASSWORD_FILE": str(secret),
                    "REDIS_URL": "redis://redis:6379/0",
                    "ALLOWED_ORIGINS": "https://platform.example.com",
                })

    def test_non_https_and_wildcard_origins_are_rejected(self):
        for origin in ("http://platform.example.com", "https://*.example.com", "https://platform.example.com/path", "https://user@platform.example.com"):
            with self.subTest(origin=origin), self.assertRaisesRegex(
                ProductionConfigurationError, "exact HTTPS"
            ):
                load_production_settings({
                    "AGENT_PLATFORM_DSN": "postgresql://platform@db/platform",
                    "REDIS_URL": "redis://redis:6379/0",
                    "ALLOWED_ORIGINS": origin,
                })


class ProductionWorkerLifecycleTests(unittest.TestCase):
    def test_worker_connects_once_and_closes_transport_on_graceful_stop(self):
        class Transport:
            def __init__(self):
                self.connect_count = 0
                self.close_count = 0

            async def connect(self):
                self.connect_count += 1

            async def close(self):
                self.close_count += 1

        class Relay:
            def __init__(self, transport):
                self.transport = transport
                self.cycles = 0

            async def publish_pending(self, *, limit):
                self.cycles += 1
                return {"claimed": 0, "published": 0, "failed": 0}

        async def scenario():
            transport = Transport()
            relay = Relay(transport)
            worker = OutboxRelayWorker(
                relay=relay, interval_seconds=0.1, batch_limit=10,
            )
            await worker.start()
            await worker.start()
            self.assertTrue(worker.running)
            await worker.stop()
            self.assertFalse(worker.running)
            self.assertEqual(transport.connect_count, 1)
            self.assertEqual(transport.close_count, 1)
            self.assertGreaterEqual(relay.cycles, 1)

        import asyncio
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
