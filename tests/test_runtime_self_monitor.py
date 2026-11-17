from __future__ import annotations

import asyncio
import unittest

from agent_platform.runtime.self_monitor import RuntimeProbe, RuntimeSelfMonitor


class RuntimeSelfMonitorTests(unittest.IsolatedAsyncioTestCase):
    async def test_probe_transitions_warning_critical_recovery(self):
        healthy = False
        transitions = []

        def check():
            if not healthy:
                raise RuntimeError("secret-bearing failure detail")

        monitor = RuntimeSelfMonitor(
            probes={"worker": RuntimeProbe("worker", check)},
            failure_threshold=2,
            on_transition=lambda name, state: transitions.append((name, state.status)),
        )
        await monitor.check_once()
        self.assertEqual(monitor.states["worker"].status, "warning")
        await monitor.check_once()
        self.assertEqual(monitor.states["worker"].status, "critical")
        healthy = True
        await monitor.check_once()
        self.assertEqual(monitor.states["worker"].status, "ok")
        self.assertEqual(transitions, [("worker", "warning"), ("worker", "critical"), ("worker", "ok")])
        self.assertNotIn("secret-bearing", str(monitor.snapshot()))

    async def test_async_probe_timeout_is_bounded_and_sanitized(self):
        async def slow():
            await asyncio.sleep(1)

        monitor = RuntimeSelfMonitor(
            probes={"queue": RuntimeProbe("queue", slow, timeout_seconds=0.001)},
            failure_threshold=1,
        )
        await monitor.check_once()
        state = monitor.states["queue"]
        self.assertEqual(state.status, "critical")
        self.assertEqual(state.error_type, "TimeoutError")

    async def test_noncritical_probe_does_not_mark_monitor_unhealthy(self):
        monitor = RuntimeSelfMonitor(
            probes={"optional": RuntimeProbe("optional", lambda: False, critical=False)},
        )
        await monitor.check_once()
        self.assertTrue(monitor.healthy)
        self.assertEqual(monitor.snapshot()["status"], "ok")

    async def test_monitor_start_stop_is_idempotent(self):
        calls = []
        monitor = RuntimeSelfMonitor(
            probes={"worker": RuntimeProbe("worker", lambda: calls.append(1))},
            interval_seconds=60,
        )
        await monitor.start()
        await monitor.start()
        await asyncio.sleep(0)
        await monitor.stop()
        await monitor.stop()
        self.assertGreaterEqual(len(calls), 1)
        self.assertIsNone(monitor._task)

    def test_invalid_probe_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            RuntimeProbe(" ", lambda: True)
        with self.assertRaises(ValueError):
            RuntimeSelfMonitor(probes={}, interval_seconds=0)
        with self.assertRaises(ValueError):
            RuntimeSelfMonitor(probes={}, failure_threshold=0)


if __name__ == "__main__":
    unittest.main()
