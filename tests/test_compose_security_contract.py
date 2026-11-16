from __future__ import annotations

import unittest
from pathlib import Path


class ComposeSecurityContractTests(unittest.TestCase):
    def test_redis_healthcheck_does_not_put_password_in_process_arguments(self):
        compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
        self.assertIn('REDISCLI_AUTH=\\\"$${REDIS_PASSWORD}\\\" redis-cli ping', compose)
        self.assertNotIn("redis-cli -a", compose)

    def test_stateful_service_ports_are_loopback_only(self):
        compose = Path("deploy/compose.yaml").read_text(encoding="utf-8")
        self.assertIn('"127.0.0.1:${POSTGRES_PORT:-5432}:5432"', compose)
        self.assertIn('"127.0.0.1:${REDIS_PORT:-6379}:6379"', compose)
        self.assertIn('"127.0.0.1:${MINIO_API_PORT:-9000}:9000"', compose)


if __name__ == "__main__":
    unittest.main()
