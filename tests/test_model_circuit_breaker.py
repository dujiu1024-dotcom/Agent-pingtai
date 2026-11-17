from __future__ import annotations

import unittest

from agent_platform.models.circuit_breaker import ModelCircuitBreaker


class ModelCircuitBreakerTests(unittest.IsolatedAsyncioTestCase):
    async def test_opens_after_threshold_and_allows_one_half_open_probe(self):
        now = [10.0]
        breaker = ModelCircuitBreaker(
            failure_threshold=2, reset_timeout_seconds=5,
            clock=lambda: now[0],
        )
        self.assertTrue(await breaker.allow_request("tenant-a:provider:model"))
        await breaker.record_failure("tenant-a:provider:model")
        self.assertTrue(await breaker.allow_request("tenant-a:provider:model"))
        await breaker.record_failure("tenant-a:provider:model")
        self.assertFalse(await breaker.allow_request("tenant-a:provider:model"))
        now[0] = 15.0
        self.assertTrue(await breaker.allow_request("tenant-a:provider:model"))
        self.assertFalse(await breaker.allow_request("tenant-a:provider:model"))
        await breaker.record_success("tenant-a:provider:model")
        self.assertTrue(await breaker.allow_request("tenant-a:provider:model"))

    async def test_failed_half_open_probe_reopens_circuit(self):
        now = [0.0]
        breaker = ModelCircuitBreaker(
            failure_threshold=1, reset_timeout_seconds=2,
            clock=lambda: now[0],
        )
        await breaker.record_failure("route")
        self.assertFalse(await breaker.allow_request("route"))
        now[0] = 2.0
        self.assertTrue(await breaker.allow_request("route"))
        await breaker.record_failure("route")
        self.assertFalse(await breaker.allow_request("route"))

    async def test_rejects_invalid_configuration(self):
        with self.assertRaises(ValueError):
            ModelCircuitBreaker(failure_threshold=0)
        with self.assertRaises(ValueError):
            ModelCircuitBreaker(reset_timeout_seconds=0)


if __name__ == "__main__":
    unittest.main()
