from datetime import datetime, time, timezone
import unittest

from agent_platform.deployment import (
    Deployment, DeploymentState, DeploymentStrategy, HealthSnapshot,
    MaintenanceWindow, build_rollout_policy,
)


class VersionedDeploymentTests(unittest.TestCase):
    def deployment(self, strategy=DeploymentStrategy.CANARY, **policy):
        return Deployment(
            tenant_id="tenant-a", service="agent-api", version="v2",
            previous_version="v1",
            policy=build_rollout_policy(strategy, **policy),
        )

    def test_canary_progresses_and_finishes_at_one_hundred(self):
        deployment = self.deployment()
        deployment.start(datetime(2026, 11, 17, 1, tzinfo=timezone.utc))
        self.assertEqual(deployment.traffic_percent, 5)
        for expected in (25, 50, 100):
            self.assertEqual(deployment.evaluate(HealthSnapshot(100, 0, 100)), DeploymentState.RUNNING)
            self.assertEqual(deployment.traffic_percent, expected)
        self.assertEqual(deployment.evaluate(HealthSnapshot(100, 0, 100)), DeploymentState.SUCCEEDED)

    def test_unhealthy_canary_automatically_restores_previous_version(self):
        deployment = self.deployment()
        deployment.start(datetime(2026, 11, 17, 1, tzinfo=timezone.utc))
        self.assertEqual(deployment.evaluate(HealthSnapshot(100, 10, 3000)), DeploymentState.ROLLED_BACK)
        self.assertEqual(deployment.traffic_percent, 0)
        self.assertEqual(deployment.history[-1]["restored_version"], "v1")

    def test_insufficient_samples_roll_back_by_default(self):
        deployment = self.deployment()
        deployment.start(datetime(2026, 11, 17, 1, tzinfo=timezone.utc))
        deployment.evaluate(HealthSnapshot(1, 0, 10))
        self.assertEqual(deployment.state, DeploymentState.ROLLED_BACK)
        self.assertEqual(deployment.rollback_reason, "insufficient_samples")

    def test_manual_rollback_when_auto_rollback_disabled(self):
        deployment = self.deployment(auto_rollback=False)
        deployment.start(datetime(2026, 11, 17, 1, tzinfo=timezone.utc))
        self.assertEqual(deployment.evaluate(HealthSnapshot(100, 10, 100)), DeploymentState.PAUSED)
        deployment.rollback("operator_requested")
        self.assertEqual(deployment.state, DeploymentState.ROLLED_BACK)

    def test_blue_green_is_single_atomic_traffic_switch(self):
        deployment = self.deployment(DeploymentStrategy.BLUE_GREEN)
        deployment.start(datetime(2026, 11, 17, 1, tzinfo=timezone.utc))
        self.assertEqual(deployment.traffic_percent, 100)
        deployment.evaluate(HealthSnapshot(100, 0, 100))
        self.assertEqual(deployment.state, DeploymentState.SUCCEEDED)

    def test_maintenance_window_gates_start_and_supports_overnight(self):
        window = MaintenanceWindow(
            weekdays=frozenset({0}), start=time(23), end=time(2), timezone_name="UTC"
        )
        deployment = self.deployment(maintenance_window=window)
        with self.assertRaisesRegex(ValueError, "outside"):
            deployment.start(datetime(2026, 11, 17, 12, tzinfo=timezone.utc))
        deployment.start(datetime(2026, 11, 17, 0, 30, tzinfo=timezone.utc))
        self.assertEqual(deployment.state, DeploymentState.RUNNING)

    def test_invalid_rollout_steps_are_rejected(self):
        with self.assertRaises(ValueError):
            build_rollout_policy(DeploymentStrategy.CANARY, canary_steps=(10, 5, 100))
        with self.assertRaises(ValueError):
            build_rollout_policy(DeploymentStrategy.GRAY, gray_steps=(5, 20))


if __name__ == "__main__":
    unittest.main()
