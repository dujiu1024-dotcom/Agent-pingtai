"""Tests for circuit breaking, bulkheads, and graceful degradation."""
import threading
import unittest

from agent_platform.resilience import (
    Bulkhead,
    BulkheadRejected,
    CircuitBreaker,
    CircuitBreakerOpen,
    CircuitState,
    DegradationPolicy,
    DependencyUnavailable,
    FailureMode,
    ResilientExecutor,
)


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class CircuitBreakerTests(unittest.TestCase):
    def test_opens_after_threshold_and_rejects_without_calling_dependency(self):
        breaker = CircuitBreaker(failure_threshold=2)
        for _ in range(2):
            with self.assertRaises(ValueError):
                breaker.call(lambda: (_ for _ in ()).throw(ValueError("down")))
        self.assertEqual(breaker.state, CircuitState.OPEN)
        called = []
        with self.assertRaises(CircuitBreakerOpen):
            breaker.call(lambda: called.append(True))
        self.assertEqual(called, [])

    def test_half_open_success_closes_and_resets_failures(self):
        clock = FakeClock()
        breaker = CircuitBreaker(1, 5, clock=clock)
        with self.assertRaises(OSError):
            breaker.call(lambda: (_ for _ in ()).throw(OSError()))
        clock.advance(5)
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)
        self.assertEqual(breaker.call(lambda: "healthy"), "healthy")
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertEqual(breaker.failure_count, 0)

    def test_half_open_failure_reopens(self):
        clock = FakeClock()
        breaker = CircuitBreaker(1, 2, clock=clock)
        with self.assertRaises(OSError):
            breaker.call(lambda: (_ for _ in ()).throw(OSError()))
        clock.advance(2)
        with self.assertRaises(OSError):
            breaker.call(lambda: (_ for _ in ()).throw(OSError()))
        self.assertEqual(breaker.state, CircuitState.OPEN)


class BulkheadTests(unittest.TestCase):
    def test_rejects_excess_concurrency_and_releases_after_error(self):
        bulkhead = Bulkhead(1)
        entered = threading.Event()
        release = threading.Event()

        def blocked():
            entered.set()
            release.wait(1)
            return "ok"

        worker = threading.Thread(target=lambda: bulkhead.call(blocked))
        worker.start()
        self.assertTrue(entered.wait(1))
        try:
            with self.assertRaises(BulkheadRejected):
                bulkhead.call(lambda: "overloaded")
        finally:
            release.set()
            worker.join(1)
        self.assertEqual(bulkhead.call(lambda: "recovered"), "recovered")

    def test_releases_slot_when_operation_raises(self):
        bulkhead = Bulkhead(1)
        with self.assertRaises(RuntimeError):
            bulkhead.call(lambda: (_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(bulkhead.call(lambda: 7), 7)


class DegradationTests(unittest.TestCase):
    def test_optional_path_uses_fallback_on_dependency_failure(self):
        executor = ResilientExecutor(
            DegradationPolicy("recommendations", FailureMode.OPTIONAL),
            CircuitBreaker(1),
        )
        self.assertEqual(
            executor.execute(
                lambda: (_ for _ in ()).throw(ConnectionError()),
                fallback=lambda _error: [],
            ),
            [],
        )

    def test_optional_path_without_fallback_is_explicitly_unavailable(self):
        executor = ResilientExecutor(
            DegradationPolicy("personalization", FailureMode.OPTIONAL)
        )
        with self.assertRaises(DependencyUnavailable):
            executor.execute(lambda: (_ for _ in ()).throw(TimeoutError()))

    def test_required_path_does_not_silently_return_fallback(self):
        executor = ResilientExecutor(DegradationPolicy("payment"))
        with self.assertRaises(ConnectionError):
            executor.execute(lambda: (_ for _ in ()).throw(ConnectionError()))

    def test_required_path_can_use_explicit_contract_preserving_fallback(self):
        executor = ResilientExecutor(DegradationPolicy("core-read"))
        self.assertEqual(
            executor.execute(
                lambda: (_ for _ in ()).throw(TimeoutError()),
                fallback=lambda _error: {"source": "stale-cache"},
            ),
            {"source": "stale-cache"},
        )


if __name__ == "__main__":
    unittest.main()
