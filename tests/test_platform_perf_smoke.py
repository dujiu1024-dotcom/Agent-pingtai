from __future__ import annotations

import time
import unittest

from agent_platform.approval.policy import ActionRequest, RiskLevel, SupervisedPolicy


class PlatformPerformanceSmokeTests(unittest.TestCase):
    def test_policy_evaluation_baseline(self):
        policy = SupervisedPolicy({"read": RiskLevel.READ_ONLY})
        request = ActionRequest(
            action="read", resource="document:1",
            parameters={"query": "baseline"}, actor_id="agent-1",
        )
        started = time.perf_counter()
        for _ in range(10000):
            result = policy.evaluate(request)
            if not result.allowed:
                self.fail("read-only policy evaluation unexpectedly denied")
        elapsed = time.perf_counter() - started
        # A deliberately broad smoke ceiling; detailed load/perf tests remain deployment work.
        self.assertLess(elapsed, 5.0, f"10k policy evaluations took {elapsed:.3f}s")


if __name__ == "__main__":
    unittest.main()
