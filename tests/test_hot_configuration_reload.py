from __future__ import annotations

import unittest

from agent_platform.configuration.hot_reload import (
    ConfigurationConflict,
    ConfigurationValidationError,
    HotConfigurationManager,
)


class HotConfigurationReloadTests(unittest.IsolatedAsyncioTestCase):
    async def test_reload_is_atomic_immutable_and_notifies_listeners(self):
        audit = []
        observed = []
        manager = HotConfigurationManager(
            {"worker": {"concurrency": 2}, "feature": True},
            audit=audit.append,
        )
        manager.subscribe(lambda old, new: observed.append((old.version, new.version)))
        result = await manager.reload(
            {"worker": {"concurrency": 4}, "feature": False},
            expected_version=1,
        )
        self.assertEqual(result.snapshot.version, 2)
        self.assertEqual(manager.get("worker")["concurrency"], 4)
        self.assertEqual(observed, [(1, 2)])
        self.assertEqual(audit[0]["changed_keys"], ["feature", "worker"])
        with self.assertRaises(TypeError):
            manager.current.values["feature"] = True
        with self.assertRaises(TypeError):
            manager.current.values["worker"]["concurrency"] = 8

    async def test_stale_version_conflict_does_not_overwrite_newer_config(self):
        manager = HotConfigurationManager({"limit": 1})
        await manager.reload({"limit": 2}, expected_version=1)
        with self.assertRaises(ConfigurationConflict):
            await manager.reload({"limit": 3}, expected_version=1)
        self.assertEqual(manager.get("limit"), 2)

    async def test_validation_and_audit_failure_leave_old_snapshot_active(self):
        def validate(candidate):
            if candidate.get("limit", 0) > 10:
                raise ValueError("limit too large")

        manager = HotConfigurationManager({"limit": 1}, validator=validate)
        with self.assertRaises(ValueError):
            await manager.reload({"limit": 99}, expected_version=1)
        self.assertEqual(manager.current.version, 1)

        def broken_audit(_event):
            raise RuntimeError("audit unavailable")

        manager = HotConfigurationManager({"limit": 1}, audit=broken_audit)
        with self.assertRaisesRegex(RuntimeError, "audit unavailable"):
            await manager.reload({"limit": 2}, expected_version=1)
        self.assertEqual(manager.get("limit"), 1)
        self.assertEqual(manager.current.version, 1)

    async def test_listener_failure_does_not_rollback_committed_config(self):
        manager = HotConfigurationManager({"enabled": False})
        manager.subscribe(lambda _old, _new: (_ for _ in ()).throw(RuntimeError("listener down")))
        result = await manager.reload({"enabled": True}, expected_version=1)
        self.assertEqual(result.snapshot.version, 2)
        self.assertEqual(result.listener_errors, ("RuntimeError",))
        self.assertTrue(manager.get("enabled"))

    async def test_noop_reload_keeps_version_and_skips_audit(self):
        events = []
        manager = HotConfigurationManager({"x": 1}, audit=events.append)
        result = await manager.reload({"x": 1}, expected_version=1)
        self.assertEqual(result.snapshot.version, 1)
        self.assertEqual(events, [])

    async def test_invalid_json_and_oversized_config_are_rejected(self):
        manager = HotConfigurationManager({"x": 1}, max_bytes=16)
        with self.assertRaises(ConfigurationValidationError):
            await manager.reload({"x": float("nan")}, expected_version=1)
        with self.assertRaises(ConfigurationValidationError):
            await manager.reload({"large": "x" * 100}, expected_version=1)

    async def test_audit_contains_no_configuration_values(self):
        events = []
        manager = HotConfigurationManager({"api_key": "do-not-log"}, audit=events.append)
        await manager.reload({"api_key": "new-secret"}, expected_version=1)
        self.assertNotIn("new-secret", str(events))
        self.assertNotIn("do-not-log", str(events))


if __name__ == "__main__":
    unittest.main()
