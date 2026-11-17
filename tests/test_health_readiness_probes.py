"""Unit tests for health, database and external API readiness probes."""
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from agent_platform.health.checks import (
    HealthProbeRegistry, ProbeStatus, database_connection_probe,
    http_dependency_probe,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeCursor:
    def fetchone(self):
        return (1,)


class FakeConnection:
    def __init__(self, fail=False):
        self.fail = fail
        self.closed = False

    def execute(self, sql):
        if self.fail:
            raise RuntimeError("sensitive database details")
        if sql != "SELECT 1":
            raise AssertionError(sql)
        return FakeCursor()

    def close(self):
        self.closed = True


class HealthProbeTests(unittest.TestCase):
    def test_all_critical_dependencies_up_means_ready(self):
        registry = HealthProbeRegistry()
        registry.register("database", lambda: None)
        registry.register("payments", lambda: None)
        report = registry.run(NOW)
        self.assertTrue(report.ready)
        self.assertEqual(report.status, ProbeStatus.UP)
        self.assertTrue(report.live)
        self.assertEqual(len(report.probes), 2)

    def test_database_probe_executes_validation_query_and_closes(self):
        connection = FakeConnection()
        database_connection_probe(lambda: connection)()
        self.assertTrue(connection.closed)

    def test_database_failure_is_reported_without_leaking_error(self):
        registry = HealthProbeRegistry()
        registry.register("database", database_connection_probe(
            lambda: FakeConnection(fail=True)
        ))
        report = registry.run(NOW)
        self.assertFalse(report.ready)
        self.assertEqual(report.failed_critical, ("database",))
        self.assertNotIn("sensitive", str(report.as_dict()))

    def test_optional_dependency_failure_degrades_but_does_not_block_readiness(self):
        registry = HealthProbeRegistry()
        registry.register("database", lambda: None)
        registry.register("analytics", lambda: (_ for _ in ()).throw(OSError("secret")),
                          critical=False)
        report = registry.run(NOW)
        self.assertTrue(report.ready)
        self.assertEqual(report.status, ProbeStatus.DEGRADED)

    def test_duplicate_names_and_naive_timestamps_rejected(self):
        registry = HealthProbeRegistry()
        registry.register("db", lambda: None)
        with self.assertRaises(ValueError):
            registry.register("db", lambda: None)
        with self.assertRaises(ValueError):
            registry.run(datetime(2026, 1, 1))

    def test_http_probe_accepts_expected_status_and_rejects_unexpected(self):
        class Response:
            status = 204
            def __enter__(self): return self
            def __exit__(self, *_): return False
        with patch("agent_platform.health.checks.urlopen", return_value=Response()):
            http_dependency_probe("https://api.example.test/health",
                                  expected_statuses=(204,))()
        with patch("agent_platform.health.checks.urlopen", return_value=Response()):
            with self.assertRaises(RuntimeError):
                http_dependency_probe("https://api.example.test/health")()

    def test_http_probe_validates_url_and_timeout(self):
        with self.assertRaises(ValueError):
            http_dependency_probe("file:///etc/passwd")
        with self.assertRaises(ValueError):
            http_dependency_probe("https://api.example.test/health", timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
